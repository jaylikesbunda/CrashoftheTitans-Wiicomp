//! Soundtrack browser: reads the music out of the prepared game's `sound.rcf` and decodes it to WAV.
//!
//! `sound.rcf` is an archive of `.rsd` files. Each starts with an `RSD6` header: codec tag, channel count, sample rate, then the
//! studio's original source path (which gives every track a readable name), and the audio from byte 0x800. The codec used
//! (`RADP`, "Radical ADPCM") is plain IMA ADPCM in 20-byte frames per channel: a 4-byte header (step index and previous sample,
//! both little-endian s16) followed by 16 bytes holding 32 four-bit samples, low nibble first. With several channels the frame
//! headers are stored together and the data bytes alternate between channels (layout as in vgmstream's `decode_rad_ima`).
//!
//! Titles: the files carry no title metadata, and most music is named `crash_idea<NN>_<role>` (many ideas share a role such as
//! "platform" or "fight"). The game's music scripts (`sound\zscripts\l<world>_e<n>.p3d`, `music.p3d` for the menus) say where
//! each stream plays, so a track is titled by its role and where it plays ("Platform · Episode 9"), numbered when two would
//! still read the same.

use serde::Serialize;
use std::{fs, io::{Read, Seek, SeekFrom}, path::Path};

const RSD_HEADER: usize = 0x800;
const FRAME_SAMPLES: usize = 32;

#[derive(Clone, Serialize)]
pub struct Track {
    pub id: usize,
    pub category: String,
    /// Unique within its category.
    pub name: String,
    /// What the piece is ("Platform", "Boss Fight"), and where the game plays it ("Episodes 1–3", "Menus"; empty if unused).
    pub role: String,
    pub place: String,
    pub file: String,
    /// Position in Deki's favourites, when it is one.
    pub favourite: Option<usize>,
    /// First episode the game plays it in (0 for menus, u32::MAX when unused); orders "Episode 2" before "Episode 10".
    pub first_episode: u32,
    pub seconds: f64,
    pub channels: u32,
    pub rate: u32,
}

#[derive(Clone)]
pub(crate) struct RcfMember { pub(crate) name: String, pub(crate) offset: u64, pub(crate) len: u64 }

fn be32(b: &[u8], at: usize) -> Result<u32, String> {
    b.get(at..at + 4).map(|x| u32::from_be_bytes(x.try_into().unwrap())).ok_or_else(|| "Truncated archive header".to_string())
}

/// Members of an RCF archive in on-disc order.
pub(crate) fn rcf_members(path: &Path) -> Result<Vec<RcfMember>, String> {
    let mut f = fs::File::open(path).map_err(|e| format!("Could not open {}: {e}", path.display()))?;
    let len = f.metadata().map_err(|e| e.to_string())?.len();
    let mut header = [0u8; 60];
    f.read_exact(&mut header).map_err(|e| e.to_string())?;
    if !header.starts_with(b"ATG CORE CEMENT LIBRARY\0") { return Err("Unsupported archive format.".into()); }
    let (index, index_size, names_off, names_size, count) = (be32(&header, 36)? as u64, be32(&header, 40)? as u64, be32(&header, 44)? as u64, be32(&header, 48)? as usize, be32(&header, 56)? as usize);
    if count > 100_000 || index_size != count as u64 * 12 || index + index_size > len || names_off + names_size as u64 > len { return Err("Archive index is out of bounds.".into()); }
    f.seek(SeekFrom::Start(index)).map_err(|e| e.to_string())?;
    let mut ib = vec![0u8; index_size as usize];
    f.read_exact(&mut ib).map_err(|e| e.to_string())?;
    let mut recs: Vec<(u64, u64)> = ib.chunks_exact(12).map(|r| (u32::from_be_bytes(r[4..8].try_into().unwrap()) as u64, u32::from_be_bytes(r[8..12].try_into().unwrap()) as u64)).collect();
    recs.sort_by_key(|r| r.0);
    f.seek(SeekFrom::Start(names_off)).map_err(|e| e.to_string())?;
    let mut names = vec![0u8; names_size];
    f.read_exact(&mut names).map_err(|e| e.to_string())?;
    let mut cursor = 8usize;
    let mut out = Vec::with_capacity(count);
    for (offset, len) in recs {
        let meta = names.get(cursor..cursor + 16).ok_or("Truncated name table")?;
        let name_len = u32::from_le_bytes(meta[12..16].try_into().unwrap()) as usize;
        cursor += 16;
        if !(2..=1024).contains(&name_len) || cursor + name_len + 3 > names.len() { return Err("Invalid member name.".into()); }
        out.push(RcfMember { name: String::from_utf8_lossy(&names[cursor..cursor + name_len - 1]).into_owned(), offset, len });
        cursor += name_len - 1 + 4;
    }
    Ok(out)
}

struct RsdHeader { channels: u32, rate: u32, source: String, data_len: u64 }

fn parse_rsd(head: &[u8], total_len: u64) -> Option<RsdHeader> {
    if head.len() < 0x118 || &head[0..4] != b"RSD6" || &head[4..8] != b"RADP" { return None; }
    let channels = u32::from_le_bytes(head[8..12].try_into().ok()?);
    let rate = u32::from_le_bytes(head[16..20].try_into().ok()?);
    if !(1..=2).contains(&channels) || !(8000..=48000).contains(&rate) || total_len < RSD_HEADER as u64 { return None; }
    let raw = &head[0x18..0x118];
    let end = raw.iter().position(|&b| b == 0).unwrap_or(raw.len());
    Some(RsdHeader { channels, rate, source: String::from_utf8_lossy(&raw[..end]).replace('\\', "/"), data_len: total_len - RSD_HEADER as u64 })
}

/// Deki's favourites, in playlist order (file names as stored on the disc).
pub const FAVOURITES: &[&str] = &[
    "crash_idea79_complete.wav", "crash_level_completed_2.wav", "crash_level_completed.wav", "crash_idea86_mojo.wav",
    "crash_idea76_slide.wav", "crash_idea100b_shellephant.wav", "tempmusic1.wav", "crash_test_track01.wav",
    "crash_test_track02.wav", "crash_idea82_voodoo.wav", "crash_idea100b_koala_bbq.wav", "crash_idea_stairs.wav",
    "crash_idea77_sludge_complete.wav",
];

/// Splits glued letters and digits ("part1" -> "part", "1"; "track01" -> "track", "01").
fn split_digits(word: &str) -> Vec<String> {
    let letters = word.chars().take_while(|c| c.is_ascii_alphabetic()).count();
    if letters < 3 || letters == word.chars().count() { return vec![word.to_string()]; }
    let mut out: Vec<String> = Vec::new();
    for c in word.chars() {
        match out.last_mut() {
            Some(last) if last.chars().last().map(|p| p.is_ascii_digit() == c.is_ascii_digit()).unwrap_or(false) => last.push(c),
            _ => out.push(c.to_string()),
        }
    }
    out
}

fn tidy_word(w: &str) -> String {
    match w.to_ascii_lowercase().as_str() {
        "bbq" => "BBQ".into(),
        "tempmusic" => "Temp Music".into(),
        lower => { let mut c = lower.chars(); c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default() }
    }
}

/// "crash_idea03b_boss_fight" -> "Boss Fight"; "crash_idea107" -> "Idea 107"; names without the prefix are just tidied.
fn display_name(stem: &str) -> String {
    let mut words: Vec<&str> = stem.split(|c| c == '_' || c == ' ').filter(|w| !w.is_empty()).collect();
    if words.first().map(|w| w.eq_ignore_ascii_case("crash")).unwrap_or(false) { words.remove(0); }
    let mut idea = None;
    if let Some(w) = words.first() {
        let w = w.to_ascii_lowercase();
        if w.starts_with("idea") && w[4..].chars().next().map(|c| c.is_ascii_digit()).unwrap_or(false) { idea = Some(w[4..].to_string()); words.remove(0); }
        else if w == "idea" && words.len() > 1 { words.remove(0); }
    }
    let title: Vec<String> = words.iter().flat_map(|w| split_digits(w)).map(|w| tidy_word(&w)).collect();
    if !title.is_empty() { title.join(" ") } else if let Some(n) = idea { format!("Idea {n}") } else { stem.to_string() }
}

/// "Episode 9", "Episodes 1–3, 9", "Menus" or "" from where a stream is used (episode numbers; 0 is the menu script).
fn place_name(mut used: Vec<u32>) -> String {
    used.sort_unstable();
    used.dedup();
    let menus = used.first() == Some(&0);
    let eps: Vec<u32> = used.into_iter().filter(|&e| e > 0).collect();
    if eps.is_empty() { return if menus { "Menus".into() } else { String::new() }; }
    let mut runs = Vec::new();
    let mut i = 0;
    while i < eps.len() {
        let mut j = i;
        while j + 1 < eps.len() && eps[j + 1] == eps[j] + 1 { j += 1; }
        runs.push(if i == j { eps[i].to_string() } else { format!("{}–{}", eps[i], eps[j]) });
        i = j + 1;
    }
    format!("{} {}", if eps.len() == 1 { "Episode" } else { "Episodes" }, runs.join(", "))
}

/// Episode number of a music script (`sound\zscripts\l3_e2b.p3d` -> 10), 0 for the menu script, None for anything else.
fn script_place(member: &str) -> Option<u32> {
    let lower = member.to_ascii_lowercase().replace('\\', "/");
    let base = lower.strip_prefix("sound/zscripts/")?.strip_suffix(".p3d")?;
    if base == "music" { return Some(0); }
    let b = base.as_bytes();
    if b.len() >= 5 && b[0] == b'l' && b[1].is_ascii_digit() && &b[2..4] == b"_e" && b[4].is_ascii_digit() {
        return Some((b[1] - b'0') as u32 * 4 - 4 + (b[4] - b'0') as u32);
    }
    None
}

/// Every stream a script names (`...\music\crash_idea53_fight` -> "crash_idea53_fight"), lower-case.
fn script_streams(data: &[u8]) -> Vec<String> {
    let mut out = Vec::new();
    for marker in [&br"\music\"[..], &br"\ambience\"[..]] {
        let mut at = 0;
        while let Some(i) = data[at..].windows(marker.len()).position(|w| w.eq_ignore_ascii_case(marker)) {
            let start = at + i + marker.len();
            let end = data[start..].iter().position(|&b| !(32..127).contains(&b)).map(|n| start + n).unwrap_or(data.len());
            if end > start { out.push(String::from_utf8_lossy(&data[start..end]).to_ascii_lowercase()); }
            at = end.max(start);
        }
    }
    out
}

fn category_of(source: &str) -> String {
    let parts: Vec<&str> = source.split('/').collect();
    match parts.iter().position(|p| p.eq_ignore_ascii_case("sounds")) {
        Some(i) if i + 1 < parts.len().saturating_sub(1) => parts[i + 1].to_ascii_lowercase(),
        _ => parts.iter().rev().nth(1).map(|s| s.to_ascii_lowercase()).unwrap_or_default(),
    }
}

/// Every music and ambience track in the prepared game folder, sorted by name.
pub fn list_tracks(game_dir: &Path) -> Result<Vec<Track>, String> {
    let path = game_dir.join("files/sound.rcf");
    let members = rcf_members(&path)?;
    let mut f = fs::File::open(&path).map_err(|e| e.to_string())?;
    // Where each stream plays, from the game's music scripts.
    let mut places: std::collections::HashMap<String, Vec<u32>> = std::collections::HashMap::new();
    for m in &members {
        let Some(place) = script_place(&m.name) else { continue };
        if m.len > 16 << 20 { continue; }
        let mut data = vec![0u8; m.len as usize];
        f.seek(SeekFrom::Start(m.offset)).map_err(|e| e.to_string())?;
        f.read_exact(&mut data).map_err(|e| e.to_string())?;
        for stream in script_streams(&data) { places.entry(stream).or_default().push(place); }
    }
    let mut tracks = Vec::new();
    let mut head = vec![0u8; 0x118];
    for (id, m) in members.iter().enumerate() {
        if !m.name.to_ascii_lowercase().ends_with(".rsd") || m.len < 0x118 { continue; }
        f.seek(SeekFrom::Start(m.offset)).map_err(|e| e.to_string())?;
        f.read_exact(&mut head).map_err(|e| e.to_string())?;
        let Some(h) = parse_rsd(&head, m.len) else { continue };
        let category = category_of(&h.source);
        if category != "music" && category != "ambience" { continue; }
        let file = h.source.rsplit('/').next().unwrap_or("").to_string();
        let stem = file.rsplit_once('.').map(|(s, _)| s).unwrap_or(&file).to_string();
        let frame_bytes = 20 * h.channels as u64;
        let seconds = (h.data_len / frame_bytes * FRAME_SAMPLES as u64) as f64 / h.rate as f64;
        let role = display_name(&stem);
        let used = places.get(&stem.to_ascii_lowercase()).cloned().unwrap_or_default();
        let first_episode = used.iter().copied().filter(|&e| e > 0).min().or(used.iter().copied().min()).unwrap_or(u32::MAX);
        let place = place_name(used);
        let favourite = FAVOURITES.iter().position(|f| f.eq_ignore_ascii_case(&file));
        let name = if place.is_empty() { role.clone() } else { format!("{role} · {place}") };
        tracks.push(Track { id, category, name, role, place, file, favourite, first_episode, seconds, channels: h.channels, rate: h.rate });
    }
    tracks.sort_by(|a, b| (a.category.as_str(), a.role.to_lowercase(), a.first_episode, a.file.as_str())
        .cmp(&(b.category.as_str(), b.role.to_lowercase(), b.first_episode, b.file.as_str())));
    number_duplicates(&mut tracks);
    Ok(tracks)
}

/// Different pieces that would still share a title get " (2)", " (3)" in list order.
fn number_duplicates(tracks: &mut [Track]) {
    let mut seen: std::collections::HashMap<(String, String), usize> = std::collections::HashMap::new();
    for t in tracks.iter_mut() {
        let n = seen.entry((t.category.clone(), t.name.to_ascii_lowercase())).or_insert(0);
        *n += 1;
        if *n > 1 { t.name = format!("{} ({})", t.name, n); }
    }
}

fn clamp16(v: i32) -> i32 { v.clamp(-32768, 32767) }

const STEP: [i32; 89] = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411,
    1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
];
const INDEX_ADJUST: [i32; 16] = [-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8];

/// Decode RADP data (everything after the 0x800 header) to interleaved 16-bit samples.
pub fn decode_radp(data: &[u8], channels: usize) -> Vec<i16> {
    let block = 20 * channels;
    let blocks = data.len() / block;
    let mut out = vec![0i16; blocks * FRAME_SAMPLES * channels];
    for b in 0..blocks {
        let frame = &data[b * block..(b + 1) * block];
        for ch in 0..channels {
            let mut index = (i16::from_le_bytes([frame[4 * ch], frame[4 * ch + 1]]) as i32).clamp(0, 88);
            let mut hist = i16::from_le_bytes([frame[4 * ch + 2], frame[4 * ch + 3]]) as i32;
            for i in 0..FRAME_SAMPLES {
                let byte = frame[4 * channels + ch + (i / 2) * channels];
                let code = ((byte >> if i & 1 == 1 { 4 } else { 0 }) & 0xf) as usize;
                let step = STEP[index as usize];
                let mut delta = step >> 3;
                if code & 1 != 0 { delta += step >> 2; }
                if code & 2 != 0 { delta += step >> 1; }
                if code & 4 != 0 { delta += step; }
                if code & 8 != 0 { delta = -delta; }
                hist = clamp16(hist + delta);
                index = (index + INDEX_ADJUST[code]).clamp(0, 88);
                out[(b * FRAME_SAMPLES + i) * channels + ch] = hist as i16;
            }
        }
    }
    out
}

pub fn wav_bytes(samples: &[i16], channels: u16, rate: u32) -> Vec<u8> {
    let data_len = (samples.len() * 2) as u32;
    let mut w = Vec::with_capacity(44 + samples.len() * 2);
    w.extend_from_slice(b"RIFF");
    w.extend_from_slice(&(36 + data_len).to_le_bytes());
    w.extend_from_slice(b"WAVEfmt ");
    w.extend_from_slice(&16u32.to_le_bytes());
    w.extend_from_slice(&1u16.to_le_bytes());
    w.extend_from_slice(&channels.to_le_bytes());
    w.extend_from_slice(&rate.to_le_bytes());
    w.extend_from_slice(&(rate * channels as u32 * 2).to_le_bytes());
    w.extend_from_slice(&(channels * 2).to_le_bytes());
    w.extend_from_slice(&16u16.to_le_bytes());
    w.extend_from_slice(b"data");
    w.extend_from_slice(&data_len.to_le_bytes());
    for s in samples { w.extend_from_slice(&s.to_le_bytes()); }
    w
}

/// Decode one archive member to a complete WAV file.
pub fn track_wav(game_dir: &Path, id: usize) -> Result<Vec<u8>, String> {
    let path = game_dir.join("files/sound.rcf");
    let members = rcf_members(&path)?;
    let m = members.get(id).ok_or("No such track.")?;
    let mut f = fs::File::open(&path).map_err(|e| e.to_string())?;
    f.seek(SeekFrom::Start(m.offset)).map_err(|e| e.to_string())?;
    let mut raw = vec![0u8; m.len as usize];
    f.read_exact(&mut raw).map_err(|e| e.to_string())?;
    let h = parse_rsd(&raw, m.len).ok_or("This file is not a supported audio track.")?;
    let samples = decode_radp(&raw[RSD_HEADER..], h.channels as usize);
    Ok(wav_bytes(&samples, h.channels as u16, h.rate))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_are_tidied() {
        assert_eq!(display_name("crash_idea03b_boss_fight"), "Boss Fight");
        assert_eq!(display_name("crash_idea100b_koala_bbq"), "Koala BBQ");
        assert_eq!(display_name("tempmusic1"), "Temp Music 1");
        assert_eq!(display_name("crash_idea43_part1"), "Part 1");
        assert_eq!(display_name("crash_test_track01"), "Test Track 01");
        assert_eq!(display_name("crash_idea107"), "Idea 107");
        assert_eq!(display_name("l3_e1_temp_ambience"), "L3 E1 Temp Ambience");
        assert_eq!(display_name("crash_idea_stairs"), "Stairs");
        assert_eq!(category_of("d:/depot/bld1/stable/production/in_game_art/sound/sounds/music/x.wav"), "music");
    }

    #[test]
    fn places_read_like_the_game_uses_them() {
        assert_eq!(script_place(r"sound\zscripts\l3_e2b.p3d"), Some(10));
        assert_eq!(script_place(r"sound\zscripts\music.p3d"), Some(0));
        assert_eq!(script_place(r"sound\rsd\a\x.rsd"), None);
        assert_eq!(place_name(vec![9, 1, 2, 3, 3]), "Episodes 1–3, 9");
        assert_eq!(place_name(vec![12]), "Episode 12");
        assert_eq!(place_name(vec![0]), "Menus");
        assert_eq!(place_name(vec![]), "");
        let data = b"xxSeqEventStream#sounds\\music\\crash_idea53_fight\x00\x01sounds\\Ambience\\cave_01\x00";
        assert_eq!(script_streams(data), vec!["crash_idea53_fight".to_string(), "cave_01".to_string()]);
    }

    #[test]
    fn duplicate_titles_are_numbered() {
        let t = |name: &str, file: &str| Track { id: 0, category: "music".into(), name: name.into(), role: String::new(),
            place: String::new(), file: file.into(), favourite: None, first_episode: 0, seconds: 0.0, channels: 2, rate: 32000 };
        let mut v = vec![t("Fight · Episode 9", "a"), t("Fight · Episode 9", "b"), t("Jack", "c")];
        number_duplicates(&mut v);
        assert_eq!(v.iter().map(|t| t.name.as_str()).collect::<Vec<_>>(), vec!["Fight · Episode 9", "Fight · Episode 9 (2)", "Jack"]);
    }

    #[test]
    fn ima_frame_matches_a_hand_decode() {
        // One mono frame: step index 0, history 0, then codes 7,7,7,7... (low nibble first): each adds step*(1+1/2+1/4+1/8)
        let mut data = vec![0u8; 20];
        for b in &mut data[4..] { *b = 0x77; }
        let pcm = decode_radp(&data, 1);
        assert_eq!(pcm.len(), 32);
        assert_eq!(pcm[0], 7 + 3 + 1 + 0 + 0); // step 7: 7>>3=0, +1(>>2)+3(>>1)+7 = 11
        assert!(pcm.windows(2).all(|w| w[1] >= w[0]), "monotonic rise");
    }

    /// Needs a prepared game: CRASH_TEST_GAME=<folder holding files/sound.rcf>. Prints stats for the first music track.
    #[test]
    fn decodes_a_real_track() {
        let Some(game) = std::env::var_os("CRASH_TEST_GAME") else { return };
        let game = Path::new(&game);
        let tracks = list_tracks(game).unwrap();
        let music: Vec<_> = tracks.iter().filter(|t| t.category == "music").collect();
        assert!(music.len() > 50, "found {} music tracks", music.len());
        let mut names: Vec<&str> = music.iter().map(|t| t.name.as_str()).collect();
        names.sort_unstable();
        let before = names.len();
        names.dedup();
        assert_eq!(names.len(), before, "music titles are unique");
        assert_eq!(tracks.iter().filter(|t| t.favourite.is_some()).count(), FAVOURITES.len(), "every favourite is on the disc");
        for t in &music { println!("TITLE {:<40} {}", t.name, t.file); }
        let t = music.iter().find(|t| t.file.contains("boss_fight")).unwrap_or(&music[0]);
        let wav = track_wav(game, t.id).unwrap();
        let pcm: Vec<i16> = wav[44..].chunks_exact(2).map(|b| i16::from_le_bytes([b[0], b[1]])).collect();
        let rms = (pcm.iter().map(|&s| (s as f64).powi(2)).sum::<f64>() / pcm.len() as f64).sqrt();
        let clipped = pcm.iter().filter(|&&s| s == i16::MAX || s == i16::MIN).count();
        println!("TRACK {} {:.1}s {} ch {} Hz rms {:.0} clipped {}", t.file, t.seconds, t.channels, t.rate, rms, clipped);
        std::fs::write(std::env::var("CRASH_TEST_WAV").unwrap_or_else(|_| "track.wav".into()), &wav).unwrap();
        assert!(rms > 200.0 && rms < 20000.0);
    }
}
