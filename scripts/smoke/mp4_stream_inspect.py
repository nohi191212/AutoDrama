from __future__ import annotations

import sys
from pathlib import Path


def read_u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def iter_boxes(data: bytes, start: int, end: int):
    offset = start
    while offset + 8 <= end:
        size = read_u32(data, offset)
        box_type = data[offset + 4 : offset + 8].decode("latin1", errors="replace")
        header = 8
        if size == 1:
            if offset + 16 > end:
                break
            size = int.from_bytes(data[offset + 8 : offset + 16], "big")
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            break
        yield box_type, offset, offset + size, offset + header
        offset += size


def find_child(data: bytes, start: int, end: int, wanted: str):
    for box_type, box_start, box_end, content_start in iter_boxes(data, start, end):
        if box_type == wanted:
            return box_start, box_end, content_start
    return None


def handler_type(data: bytes, mdia_start: int, mdia_end: int) -> str | None:
    hdlr = find_child(data, mdia_start, mdia_end, "hdlr")
    if not hdlr:
        return None
    _, hdlr_end, content_start = hdlr
    if content_start + 12 > hdlr_end:
        return None
    return data[content_start + 8 : content_start + 12].decode("latin1", errors="replace")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: mp4_stream_inspect.py PATH")
    path = Path(sys.argv[1])
    data = path.read_bytes()
    moov = find_child(data, 0, len(data), "moov")
    if not moov:
        raise SystemExit("moov=missing")
    _, moov_end, moov_content = moov
    handlers: list[str] = []
    for box_type, trak_start, trak_end, trak_content in iter_boxes(data, moov_content, moov_end):
        if box_type != "trak":
            continue
        mdia = find_child(data, trak_content, trak_end, "mdia")
        if not mdia:
            continue
        _, mdia_end, mdia_content = mdia
        handler = handler_type(data, mdia_content, mdia_end)
        if handler:
            handlers.append(handler)
    print("tracks=" + ",".join(handlers))
    print("has_video=" + str("vide" in handlers).lower())
    print("has_audio=" + str("soun" in handlers).lower())


if __name__ == "__main__":
    main()
