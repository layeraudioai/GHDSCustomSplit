"""Automated splitting and processing script for GHDSCustoms."""
import argparse
import os
import subprocess
import sys

# Import ChartSplitter from the adjacent split.py
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
try:
    from split import ChartSplitter
except ImportError:
    print("Error: Could not import 'ChartSplitter' from 'split.py'.")
    print("Ensure split.py is in the same directory as this script.")
    sys.exit(1)

def main():
    parser = argparse.ArgumentParser(
        description='Split chart into segments and iteratively run GHDSCustoms pipeline.'
    )
    parser.add_argument('chart', help='Path to the input .chart file')
    parser.add_argument('audio', help='Path to the input .wav file')
    parser.add_argument('ghds_path', help='Path to the GHDSCustoms folder (containing run.bat)')
    
    args = parser.parse_args()

    # Verify paths
    if not os.path.exists(args.chart):
        print(f"Error: Chart file not found at {args.chart}")
        sys.exit(1)
    if not os.path.exists(args.audio):
        print(f"Error: Audio file not found at {args.audio}")
        sys.exit(1)
    if not os.path.isdir(args.ghds_path):
        print(f"Error: GHDSCustoms directory not found at {args.ghds_path}")
        sys.exit(1)
    
    bat_file = os.path.join(args.ghds_path, "run.sh")
    if not os.path.exists(bat_file):
        print(f"Error: run.bat not found in {args.ghds_path}")
        sys.exit(1)
        
    target_chart = os.path.join(args.ghds_path, "notes.chart")
    target_audio = os.path.join(args.ghds_path, "song.wav")

    # Initialize Splitter
    try:
        splitter = ChartSplitter(args.chart, args.audio)
    except Exception as e:
        print(f"Error initializing splitter: {e}")
        sys.exit(1)
        
    print("Calculating splits...")
    splits = splitter.calculate_splits()
    total_parts = len(splits) - 1
    print(f"Detected {total_parts} segments.")

    for i in range(total_parts):
        print(f"\n========================================")
        print(f"Processing Segment {i+1} of {total_parts}")
        print(f"========================================")
        
        start_tick = splits[i]
        end_tick = splits[i+1]
        
        # 1. Generate and Save Chart Segment
        print(f"Generating chart segment (Ticks {start_tick}-{end_tick})...")
        part_chart = splitter.create_split_chart(start_tick, end_tick)
        with open(target_chart, 'w') as f:
            part_chart.dump(f)

        # 2. Generate and Save Audio Segment
        print("Generating audio segment...")
        start_ms = int(splitter.tick_to_seconds(start_tick) * 1000)
        end_ms = int(splitter.tick_to_seconds(end_tick) * 1000)
        
        if end_ms > len(splitter.audio):
            end_ms = len(splitter.audio)
            
        part_audio = splitter.audio[start_ms:end_ms]
        part_audio.export(target_audio, format="wav")
            
        print(f"Segment files placed in {args.ghds_path}")
        print("Launching GHDSCustoms tool...")
        
        # 3. Run run.bat
        try:
            subprocess.check_call([bat_file], cwd=args.ghds_path, shell=True)
        except subprocess.CalledProcessError as e:
            print(f"run.bat exited with error code {e.returncode}")
            # Script continues to next iteration unless user interrupts

    print("\nAll segments processed successfully.")

if __name__ == "__main__":
    main()