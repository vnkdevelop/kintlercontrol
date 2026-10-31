import os
import sys

from desktop import mcp_entry


def test_mcp_transport_cannot_close_original_stdio(monkeypatch):
    previous = (sys.stdin, sys.stdout)
    input_read, input_write = os.pipe()
    output_read, output_write = os.pipe()
    original_in = os.fdopen(input_read, "r", encoding="utf-8")
    original_out = os.fdopen(output_write, "w", encoding="utf-8")

    def server():
        sys.stdin.buffer.close()
        sys.stdout.buffer.close()

    monkeypatch.setattr(sys, "stdin", original_in)
    monkeypatch.setattr(sys, "stdout", original_out)
    monkeypatch.setattr(sys, "argv", ["KintlerMCP"])
    monkeypatch.setattr(mcp_entry, "serve", server)
    try:
        mcp_entry.main()
        assert sys.stdin is original_in and not original_in.closed
        assert sys.stdout is original_out and not original_out.closed
        original_out.write("RESTORED\n")
        original_out.flush()
        assert b"RESTORED" in os.read(output_read, 100)
    finally:
        sys.stdin, sys.stdout = previous
        original_in.close()
        original_out.close()
        os.close(input_write)
        os.close(output_read)
