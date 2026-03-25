"""Split chart and audio files based on constraints."""
import argparse
import copy
import os
import sys

try:
    from pydub import AudioSegment
except ImportError:
    print("Please install pydub: pip install pydub")
    sys.exit(1)

import chparse
from chparse import flags
from chparse.note import SyncEvent, Note, Event
from chparse.chart import Chart
from chparse.instrument import Instrument

class ChartSplitter:
    """Splits chart and audio into chunks."""

    def __init__(self, chart_path, audio_path):
        self.chart_path = chart_path
        self.audio_path = audio_path

        print(f"Loading chart: {chart_path}")
        with open(chart_path, 'r', encoding='utf-8-sig') as f:
            self.chart = chparse.load(f)

        # Default resolution is 192 if not specified
        self.resolution = int(getattr(self.chart, 'Resolution', 192))

        print(f"Loading audio: {audio_path}")
        self.audio = AudioSegment.from_wav(audio_path)

        # Ensure sync track is sorted
        self.sync = self.chart.sync_track
        self.sync.sort(key=lambda x: x.time)

        # Cache BPM events for fast lookup
        self.bpms = [e for e in self.sync if e.kind == flags.BPM]
        if not self.bpms or self.bpms[0].time > 0:
            # Assume 120 BPM if not present at 0. Value is milli-BPM.
            self.bpms.insert(0, SyncEvent(0, flags.BPM, 120000))

    def tick_to_seconds(self, tick):
        """Convert ticks to seconds."""
        seconds = 0.0
        prev_tick = 0
        # BPM value is milli-BPM (e.g. 120000 = 120 BPM)
        # BPS = BPM / 60
        current_bps = (self.bpms[0].value / 1000.0) / 60.0

        for bpm in self.bpms:
            if bpm.time > tick:
                break

            delta = bpm.time - prev_tick
            seconds += delta / (self.resolution * current_bps)

            prev_tick = bpm.time
            current_bps = (bpm.value / 1000.0) / 60.0

        delta = tick - prev_tick
        seconds += delta / (self.resolution * current_bps)
        return seconds

    def seconds_to_tick(self, seconds):
        """Convert seconds to ticks."""
        curr_seconds = 0.0
        prev_tick = 0
        current_bps = (self.bpms[0].value / 1000.0) / 60.0

        for bpm in self.bpms:
            delta_ticks = bpm.time - prev_tick
            seg_seconds = delta_ticks / (self.resolution * current_bps)

            if curr_seconds + seg_seconds >= seconds:
                rem = seconds - curr_seconds
                needed = rem * (self.resolution * current_bps)
                return int(prev_tick + needed)

            curr_seconds += seg_seconds
            prev_tick = bpm.time
            current_bps = (bpm.value / 1000.0) / 60.0

        rem = seconds - curr_seconds
        needed = rem * (self.resolution * current_bps)
        return int(prev_tick + needed)

    def find_candidates(self):
        """Find candidate split points (silence, transitions)."""
        candidates = []

        # 1. Silences > 0.420s
        all_notes = []
        for d in self.chart.instruments.values():
            for i in d.values():
                if isinstance(i, Instrument):
                    for n in i:
                        if isinstance(n, Note):
                            all_notes.append(n)
        all_notes.sort(key=lambda x: x.time)

        if all_notes:
            max_end = all_notes[0].time + all_notes[0].length
            for n in all_notes:
                start = n.time
                if start > max_end:
                    # Gap found
                    s_sec = self.tick_to_seconds(max_end)
                    e_sec = self.tick_to_seconds(start)
                    if e_sec - s_sec > 0.420:
                        # Split in the middle of silence
                        mid_sec = (s_sec + e_sec) / 2
                        mid_tick = self.seconds_to_tick(mid_sec)
                        candidates.append((mid_tick, 'silence', 10))

                end = n.time + n.length
                if end > max_end:
                    max_end = end

        # 2. Transitions (TS, BPM)
        for e in self.sync:
            if e.time > 0:
                if e.kind == flags.TIME_SIGNATURE:
                    candidates.append((e.time, 'ts', 5))
                elif e.kind == flags.BPM:
                    candidates.append((e.time, 'bpm', 3))

        candidates.sort(key=lambda x: x[0])
        return candidates

    def calculate_splits(self):
        """Calculate optimal split points."""
        duration_sec = self.audio.duration_seconds
        splits = [0]
        current_sec = 0
        candidates = self.find_candidates()

        while current_sec < duration_sec:
            min_target = current_sec + 50
            max_target = current_sec + 250

            if min_target >= duration_sec:
                # Remainder is small, finish up
                splits.append(self.seconds_to_tick(duration_sec))
                break

            min_tick = self.seconds_to_tick(min_target)
            max_tick = self.seconds_to_tick(max_target)

            valid = [c for c in candidates if min_tick <= c[0] <= max_tick]

            best_tick = None
            if valid:
                # Pick candidate with highest score (silence > ts > bpm)
                best = max(valid, key=lambda x: x[2])
                best_tick = best[0]
            else:
                # No candidate, force split
                if max_target >= duration_sec:
                    best_tick = self.seconds_to_tick(duration_sec)
                else:
                    best_tick = max_tick

            splits.append(best_tick)
            current_sec = self.tick_to_seconds(best_tick)

        return splits

    def create_split_chart(self, start_tick, end_tick):
        """Create a new Chart object for the segment."""
        new_chart = Chart(self.chart.__dict__)
        # Reset instruments
        new_chart.instruments = {
            flags.EXPERT: {}, flags.HARD: {}, flags.MEDIUM: {},
            flags.EASY: {}, flags.NA: {}
        }

        def shift(t):
            return t - start_tick

        # Determine initial sync state
        current_bpm = 120000
        current_ts = 4

        # BPM state
        relevant_bpms = [b for b in self.bpms if b.time <= start_tick]
        if relevant_bpms:
            current_bpm = relevant_bpms[-1].value

        # TS state
        ts_events = [e for e in self.sync if e.kind == flags.TIME_SIGNATURE]
        relevant_ts = [t for t in ts_events if t.time <= start_tick]
        if relevant_ts:
            current_ts = relevant_ts[-1].value

        new_sync = Instrument(kind=flags.SYNC, difficulty=flags.NA)

        # Add initial state if not present at start_tick
        has_bpm = any(e.time == start_tick and e.kind == flags.BPM for e in self.sync)
        has_ts = any(e.time == start_tick and e.kind == flags.TIME_SIGNATURE for e in self.sync)

        if not has_bpm:
            new_sync.append(SyncEvent(0, flags.BPM, current_bpm))
        if not has_ts:
            new_sync.append(SyncEvent(0, flags.TIME_SIGNATURE, current_ts))

        for e in self.sync:
            if start_tick <= e.time < end_tick:
                new_e = copy.copy(e)
                new_e.time = shift(e.time)
                new_sync.append(new_e)

        new_chart.add_instrument(new_sync)

        # Transfer Events
        if flags.EVENTS in self.chart.instruments[flags.NA]:
            new_events = Instrument(kind=flags.EVENTS, difficulty=flags.NA)
            for e in self.chart.events:
                if start_tick <= e.time < end_tick:
                    new_e = copy.copy(e)
                    new_e.time = shift(e.time)
                    new_events.append(new_e)
            new_chart.add_instrument(new_events)

        # Transfer Instruments (Notes)
        for diff_name, diff_dict in self.chart.instruments.items():
            if diff_name == flags.NA:
                continue
            for inst_name, inst in diff_dict.items():
                new_inst = Instrument(kind=inst_name, difficulty=diff_name)
                for note in inst:
                    # Include notes that start within the window
                    if start_tick <= note.time < end_tick:
                        new_n = copy.deepcopy(note)
                        new_n.time = shift(note.time)
                        new_inst.append(new_n)
                if len(new_inst) > 0:
                    new_chart.add_instrument(new_inst)

        return new_chart

    def split(self):
        """Execute the split."""
        splits = self.calculate_splits()
        print(f"Calculated {len(splits)-1} parts.")

        base_chart = os.path.splitext(self.chart_path)[0]
        base_audio = os.path.splitext(self.audio_path)[0]

        for i in range(len(splits) - 1):
            start = splits[i]
            end = splits[i+1]

            print(f"Processing Part {i+1}: Tick {start} to {end}")

            # Process Chart
            part_chart = self.create_split_chart(start, end)
            chart_out = f"{base_chart}_part{i+1}.chart"
            with open(chart_out, 'w') as f:
                part_chart.dump(f)

            # Process Audio
            start_ms = int(self.tick_to_seconds(start) * 1000)
            end_ms = int(self.tick_to_seconds(end) * 1000)
            
            # Handle end of audio
            if end_ms > len(self.audio):
                end_ms = len(self.audio)
                
            part_audio = self.audio[start_ms:end_ms]
            audio_out = f"{base_audio}_part{i+1}.wav"
            part_audio.export(audio_out, format="wav")

            print(f"Saved {chart_out} and {audio_out}")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Split .chart and .wav files.')
    parser.add_argument('chart', help='Input .chart file path')
    parser.add_argument('audio', help='Input .wav file path')
    args = parser.parse_args()

    if not os.path.exists(args.chart):
        print(f"Chart file not found: {args.chart}")
        sys.exit(1)
    if not os.path.exists(args.audio):
        print(f"Audio file not found: {args.audio}")
        sys.exit(1)

    splitter = ChartSplitter(args.chart, args.audio)
    splitter.split()