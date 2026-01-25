import json
from typing import List
from state import TraceEvent


class TraceLogger:
    def __init__(self):
        self.events: List[TraceEvent] = []
    
    def append(self, event: TraceEvent):
        self.events.append(event)
    
    def flush_jsonl(self, path: str = "traces.jsonl"):
        with open(path, 'w') as f:
            for event in self.events:
                f.write(json.dumps(event.to_dict()) + '\n')
        print(f"Flushed {len(self.events)} events to {path}")
