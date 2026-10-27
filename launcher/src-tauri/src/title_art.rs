//! The "WIICOMP" title art, built from the player's own disc.
//!
//! The title screen logo is six paletted PNG tiles in the frontend package (512 x 352 when assembled: 256 x 256, 256 x 64 and
//! 256 x 32 rows, left and right). No game art ships with the launcher: the original tiles are read from the disc, the logo is
//! scaled to make room, the bundled WIICOMP wordmark (our own art) is placed underneath, and the tiles are cut and re-encoded.
//! The same composition, cropped, is the logo the launcher shows once the game is prepared.

use std::io::Cursor;

/// Tile placement on the assembled canvas, in package order.
pub const TILES: [(u32, u32, u32, u32); 6] = [(0, 0, 256, 256), (256, 0, 256, 256), (0, 256, 256, 64), (256, 256, 256, 64), (0, 320, 256, 32), (256, 320, 256, 32)];
const CANVAS: (u32, u32) = (512, 352);
/// The original logo is scaled by this and moved so its bottom clears the wordmark (fitted against the first WIICOMP release).
const LOGO_SCALE: f32 = 0.955;
const LOGO_OFFSET: (i32, i32) = (14, -25);
const WORDMARK_AT: (i32, i32) = (149, 272);
static WORDMARK: &[u8] = include_bytes!("../assets/wiicomp-title.png");

/// Straight-alpha RGBA8 image.
pub struct Rgba { pub w: u32, pub h: u32, pub px: Vec<u8> }

pub fn decode_png(bytes: &[u8]) -> Result<Rgba, String> {
    let mut dec = png::Decoder::new(Cursor::new(bytes));
    dec.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16 | png::Transformations::ALPHA);
    let mut reader = dec.read_info().map_err(|e| format!("Could not read a title logo tile: {e}"))?;
    let mut buf = vec![0; reader.output_buffer_size().ok_or("Title logo tile is too large.")?];
    let info = reader.next_frame(&mut buf).map_err(|e| format!("Could not decode a title logo tile: {e}"))?;
    let (w, h) = (info.width, info.height);
    let px = match info.color_type {
        png::ColorType::Rgba => buf[..(w * h * 4) as usize].to_vec(),
        png::ColorType::GrayscaleAlpha => buf[..(w * h * 2) as usize].chunks_exact(2).flat_map(|p| [p[0], p[0], p[0], p[1]]).collect(),
        other => return Err(format!("Unexpected title logo tile colour type {other:?}.")),
    };
    Ok(Rgba { w, h, px })
}

/// Premultiplied float image, the working format for scaling and compositing.
struct Pm { w: u32, h: u32, px: Vec<[f32; 4]> }

impl Pm {
    fn new(w: u32, h: u32) -> Self { Self { w, h, px: vec![[0.0; 4]; (w * h) as usize] } }
    fn from(img: &Rgba) -> Self {
        let px = img.px.chunks_exact(4).map(|p| { let a = p[3] as f32 / 255.0; [p[0] as f32 * a, p[1] as f32 * a, p[2] as f32 * a, a * 255.0] }).collect();
        Self { w: img.w, h: img.h, px }
    }
    fn to_rgba(&self) -> Rgba {
        let px = self.px.iter().flat_map(|p| {
            let a = p[3].clamp(0.0, 255.0);
            if a < 0.5 { return [0, 0, 0, 0]; }
            let k = 255.0 / a;
            [(p[0] * k).round().clamp(0.0, 255.0) as u8, (p[1] * k).round().clamp(0.0, 255.0) as u8, (p[2] * k).round().clamp(0.0, 255.0) as u8, a.round() as u8]
        }).collect();
        Rgba { w: self.w, h: self.h, px }
    }
    /// Source-over at (x, y); pixels outside the canvas are dropped.
    fn over(&mut self, src: &Pm, x: i32, y: i32) {
        for sy in 0..src.h as i32 {
            let dy = y + sy; if dy < 0 || dy >= self.h as i32 { continue; }
            for sx in 0..src.w as i32 {
                let dx = x + sx; if dx < 0 || dx >= self.w as i32 { continue; }
                let s = src.px[(sy as u32 * src.w + sx as u32) as usize];
                let d = &mut self.px[(dy as u32 * self.w + dx as u32) as usize];
                let k = 1.0 - s[3] / 255.0;
                for c in 0..4 { d[c] = s[c] + d[c] * k; }
            }
        }
    }
}

fn lanczos3(x: f32) -> f32 {
    if x == 0.0 { return 1.0; }
    if x.abs() >= 3.0 { return 0.0; }
    let px = std::f32::consts::PI * x;
    3.0 * px.sin() * (px / 3.0).sin() / (px * px)
}

/// Separable Lanczos-3 resize of one axis (premultiplied, so edges do not pick up colour from transparent pixels).
fn resize_axis(src: &Pm, new_len: u32, horizontal: bool) -> Pm {
    let (len, other) = if horizontal { (src.w, src.h) } else { (src.h, src.w) };
    let scale = len as f32 / new_len as f32;
    let support = 3.0 * scale.max(1.0);
    let mut out = if horizontal { Pm::new(new_len, src.h) } else { Pm::new(src.w, new_len) };
    for i in 0..new_len {
        let center = (i as f32 + 0.5) * scale;
        let lo = (center - support).floor().max(0.0) as i32;
        let hi = (center + support).ceil().min(len as f32) as i32;
        let mut weights: Vec<(u32, f32)> = (lo..hi).map(|j| (j as u32, lanczos3((j as f32 + 0.5 - center) / scale.max(1.0)))).collect();
        let sum: f32 = weights.iter().map(|w| w.1).sum();
        if sum != 0.0 { for w in &mut weights { w.1 /= sum; } }
        for o in 0..other {
            let mut acc = [0.0f32; 4];
            for &(j, wt) in &weights {
                let p = if horizontal { src.px[(o * src.w + j) as usize] } else { src.px[(j * src.w + o) as usize] };
                for c in 0..4 { acc[c] += p[c] * wt; }
            }
            for c in &mut acc { *c = c.max(0.0); }
            acc[3] = acc[3].min(255.0);
            for c in 0..3 { acc[c] = acc[c].min(acc[3]); }
            let at = if horizontal { o * new_len + i } else { i * src.w + o };
            out.px[at as usize] = acc;
        }
    }
    out
}

/// 256-colour paletted PNG with per-entry alpha, as the game's tile loader expects.
pub fn encode_indexed(img: &Rgba, colors: usize) -> Result<Vec<u8>, String> {
    let nq = color_quant::NeuQuant::new(10, colors, &img.px);
    let map = nq.color_map_rgba();
    let mut palette = Vec::with_capacity(colors * 3);
    let mut trns = Vec::with_capacity(colors);
    for c in map.chunks_exact(4) { palette.extend_from_slice(&c[..3]); trns.push(c[3]); }
    // Fully transparent pixels always map to one fully transparent entry.
    let clear = trns.iter().position(|&a| a == 0).unwrap_or_else(|| {
        let i = trns.iter().enumerate().min_by_key(|(_, a)| **a).map(|(i, _)| i).unwrap_or(0);
        trns[i] = 0; i
    });
    let indices: Vec<u8> = img.px.chunks_exact(4).map(|p| if p[3] == 0 { clear as u8 } else { nq.index_of(p) as u8 }).collect();
    let mut out = Vec::new();
    {
        let mut enc = png::Encoder::new(&mut out, img.w, img.h);
        enc.set_color(png::ColorType::Indexed);
        enc.set_depth(png::BitDepth::Eight);
        enc.set_palette(palette);
        enc.set_trns(trns);
        enc.set_compression(png::Compression::High);
        let mut w = enc.write_header().map_err(|e| e.to_string())?;
        w.write_image_data(&indices).map_err(|e| e.to_string())?;
    }
    Ok(out)
}

fn encode_rgba(img: &Rgba) -> Result<Vec<u8>, String> {
    let mut out = Vec::new();
    {
        let mut enc = png::Encoder::new(&mut out, img.w, img.h);
        enc.set_color(png::ColorType::Rgba);
        enc.set_depth(png::BitDepth::Eight);
        enc.set_compression(png::Compression::High);
        let mut w = enc.write_header().map_err(|e| e.to_string())?;
        w.write_image_data(&img.px).map_err(|e| e.to_string())?;
    }
    Ok(out)
}

pub struct Composed {
    /// The assembled title art (original logo scaled and moved, wordmark underneath).
    pub canvas: Rgba,
    /// The same, cropped to its visible pixels, as an RGBA PNG for the launcher.
    pub logo_png: Vec<u8>,
}

/// Builds the WIICOMP title art from the disc's six original tiles (PNG bytes in package order).
pub fn compose(originals: &[Vec<u8>]) -> Result<Composed, String> {
    if originals.len() != TILES.len() { return Err("Expected six title logo tiles.".into()); }
    let mut logo = Pm::new(CANVAS.0, CANVAS.1);
    for (png, &(x, y, w, h)) in originals.iter().zip(TILES.iter()) {
        let tile = decode_png(png)?;
        if (tile.w, tile.h) != (w, h) { return Err(format!("Title logo tile is {}x{}, expected {w}x{h}.", tile.w, tile.h)); }
        logo.over(&Pm::from(&tile), x as i32, y as i32);
    }
    let (nw, nh) = ((CANVAS.0 as f32 * LOGO_SCALE).round() as u32, (CANVAS.1 as f32 * LOGO_SCALE).round() as u32);
    let scaled = resize_axis(&resize_axis(&logo, nw, true), nh, false);
    let mut out = Pm::new(CANVAS.0, CANVAS.1);
    out.over(&scaled, LOGO_OFFSET.0, LOGO_OFFSET.1);
    out.over(&Pm::from(&decode_png(WORDMARK)?), WORDMARK_AT.0, WORDMARK_AT.1);
    let canvas = out.to_rgba();
    // Crop the launcher logo to the visible pixels.
    let (mut x0, mut y0, mut x1, mut y1) = (canvas.w, canvas.h, 0, 0);
    for y in 0..canvas.h { for x in 0..canvas.w {
        if canvas.px[((y * canvas.w + x) * 4 + 3) as usize] > 8 { x0 = x0.min(x); y0 = y0.min(y); x1 = x1.max(x + 1); y1 = y1.max(y + 1); }
    } }
    if x1 <= x0 || y1 <= y0 { return Err("The disc's title logo is empty.".into()); }
    let crop = Rgba { w: x1 - x0, h: y1 - y0, px: (y0..y1).flat_map(|y| canvas.px[((y * canvas.w + x0) * 4) as usize..((y * canvas.w + x1) * 4) as usize].to_vec()).collect() };
    Ok(Composed { logo_png: encode_rgba(&crop)?, canvas })
}

/// One tile of the composed canvas, encoded so it fits `capacity` bytes (fewer colours if needed).
pub fn tile_png(canvas: &Rgba, tile: usize, capacity: usize) -> Result<Vec<u8>, String> {
    let (x, y, w, h) = TILES[tile];
    let px = (y..y + h).flat_map(|row| canvas.px[((row * canvas.w + x) * 4) as usize..((row * canvas.w + x + w) * 4) as usize].to_vec()).collect();
    let img = Rgba { w, h, px };
    for colors in [256, 192, 128, 64] {
        let png = encode_indexed(&img, colors)?;
        if png.len() <= capacity { return Ok(png); }
    }
    Err(format!("Title logo tile {tile} does not fit its slot ({capacity} bytes)."))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tile(w: u32, h: u32, f: impl Fn(u32, u32) -> [u8; 4]) -> Vec<u8> {
        let mut px = Vec::with_capacity((w * h * 4) as usize);
        for y in 0..h { for x in 0..w { px.extend_from_slice(&f(x, y)); } }
        let img = Rgba { w, h, px };
        encode_indexed(&img, 256).unwrap()
    }

    #[test]
    fn indexed_round_trip_keeps_transparency() {
        let png = tile(16, 8, |x, _| if x < 8 { [0, 0, 0, 0] } else { [200, 40, 10, 255] });
        assert_eq!(png[25], 3, "paletted");
        let back = decode_png(&png).unwrap();
        assert_eq!(back.px[3], 0);
        assert_eq!(&back.px[(8 * 4) as usize..(8 * 4 + 4) as usize], &[200, 40, 10, 255]);
    }

    #[test]
    fn composition_moves_the_logo_up_and_adds_the_wordmark() {
        // A solid block where the original logo's bottom edge sits (rows 280..300 of the canvas).
        let originals: Vec<Vec<u8>> = TILES.iter().map(|&(x, y, w, h)| tile(w, h, |tx, ty| {
            let (cx, cy) = (x + tx, y + ty);
            if (100..400).contains(&cx) && (280..300).contains(&cy) { [255, 0, 0, 255] } else { [0, 0, 0, 0] }
        })).collect();
        let c = compose(&originals).unwrap();
        let alpha = |x: u32, y: u32| c.canvas.px[((y * c.canvas.w + x) * 4 + 3) as usize];
        assert!(alpha(250, 250) > 200, "the block moved up by about 25 rows");
        assert!((150..338).any(|x| alpha(x, 295) > 200), "wordmark sits below the logo");
        assert_eq!(alpha(10, 340), 0);
        for t in 0..6 { assert!(tile_png(&c.canvas, t, 20_000).is_ok()); }
        assert!(c.logo_png.starts_with(b"\x89PNG"));
    }
}
