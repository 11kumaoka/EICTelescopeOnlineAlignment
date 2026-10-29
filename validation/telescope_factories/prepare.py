#!/usr/bin/env python3
"""Write a single EventHeader to trigger the synthetic TrackerHit JANA factory.

No simulated or historical hits are consumed. Run in the fixed ROOT/PODIO container.
"""
import argparse
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New input ROOT file')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Output already exists')
    import ROOT
    from podio.frame import Frame
    from podio import root_io
    headers = ROOT.edm4hep.EventHeaderCollection()
    header = headers.create()
    header.setRunNumber(1)
    header.setEventNumber(0)
    frame = Frame()
    frame.put(headers, 'EventHeader')
    writer = root_io.Writer(str(args.output))
    writer.write_frame(frame, 'events')
    # Pinned podio 1.7 exposes finalization only on the underlying C++ ROOTWriter.
    writer._writer.finish()

if __name__ == '__main__':
    main()
