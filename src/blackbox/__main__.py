"""`python -m src.blackbox` — serves the blackbox tools over stdio.

Nothing may write to stdout before `run()` starts: until then stdout is the wire.
Logging is safe, since `src/logs.py` writes to stderr.
"""

from src.blackbox.server import mcp

if __name__ == '__main__':
    mcp.run()
