"""WPR stop warnings survive ETL headers which incorrectly report zero loss."""
from pathlib import Path
import json,re
def quality(directory,metadata=None):
 path=Path(directory)/'cpu-profile-control.json'
 rows=json.loads(path.read_text()) if path.is_file() else []
 dropped=max([int(m.group(1).replace(',','')) for row in rows for m in re.finditer(r'dropped\s+([\d,]+)\s+events',row.get('output',''),re.I)]+[0])
 metadata=metadata or {}
 losses=max(dropped,metadata.get('events_lost',0),metadata.get('buffers_lost',0))
 return dict(wpr_dropped_events=dropped,loss_reported=bool(losses),individual_stall_attribution_reliable=not bool(losses))
