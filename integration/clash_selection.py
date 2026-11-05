"""Read a stopped Mihomo's saved GLOBAL choice without opening or changing its DB.

Mihomo stores choices in bbolt's `selected` bucket. Reading a verified v2 snapshot
before spawning it distinguishes a real choice (including DIRECT) from an empty
cache created by a config preflight. Unknown/corrupt formats return no choice.
"""
from pathlib import Path
import struct
import sys

MAX_BYTES = 128 * 1024 * 1024


def saved_global(path):
    try:
        path = Path(path)
        size = path.stat().st_size
        if not 8192 <= size <= MAX_BYTES:
            return ''
        raw = path.read_bytes()
        page_size = struct.unpack_from('<I', raw, 24)[0]
        if page_size < 1024 or page_size > 65536 or page_size & (page_size - 1):
            return ''

        def meta(index):
            start = index * page_size
            page_id, flags = struct.unpack_from('<QH', raw, start)
            data = raw[start + 16:start + 80]
            magic, version, ps, _, root, _, _, high, txid, checksum = struct.unpack('<IIIIQQQQQQ', data)
            hashed = 14695981039346656037
            for byte in data[:56]:
                hashed = ((hashed ^ byte) * 1099511628211) & ((1 << 64) - 1)
            if (page_id != index or flags != 4 or magic != 0xED0CDAED or version != 2
                    or ps != page_size or checksum != hashed or high * ps > len(raw)):
                raise ValueError('invalid metadata')
            return txid, root, high

        candidates = []
        for index in (0, 1):
            try:
                candidates.append(meta(index))
            except (ValueError, struct.error):
                pass
        if not candidates:
            return ''
        _, root, high = max(candidates)
        visited = set()

        def lookup(page_id, key, inline=None):
            if inline is None:
                if page_id < 2 or page_id >= high or page_id in visited:
                    raise ValueError('invalid page')
                visited.add(page_id)
                start = page_id * page_size
                stored_id, flags, count, overflow = struct.unpack_from('<QHHI', raw, start)
                if stored_id != page_id or page_id + overflow >= high:
                    raise ValueError('invalid extent')
                page = raw[start:start + (overflow + 1) * page_size]
            else:
                page = inline
                _, flags, count, overflow = struct.unpack_from('<QHHI', page)
                if overflow:
                    raise ValueError('invalid inline page')
            if len(visited) > 64 or 16 + count * 16 > len(page):
                raise ValueError('invalid page size')
            branch = None
            if flags not in (1, 2):
                raise ValueError('unsupported page')
            for index in range(count):
                offset = 16 + index * 16
                if flags == 1:
                    pos, key_size, child = struct.unpack_from('<IIQ', page, offset)
                    value_size, item_flags = 0, 0
                else:
                    item_flags, pos, key_size, value_size = struct.unpack_from('<IIII', page, offset)
                begin = offset + pos
                end = begin + key_size + value_size
                if begin < 16 + count * 16 or end > len(page):
                    raise ValueError('invalid item')
                item_key = page[begin:begin + key_size]
                if flags == 1:
                    if item_key <= key:
                        branch = child
                    else:
                        break
                elif item_key == key:
                    return item_flags, page[begin + key_size:end]
            if branch is not None:
                return lookup(branch, key)
            return None

        bucket = lookup(root, b'selected')
        if not bucket or bucket[0] != 1 or len(bucket[1]) < 16:
            return ''
        bucket_root = struct.unpack_from('<Q', bucket[1])[0]
        selected = lookup(bucket_root, b'GLOBAL', bucket[1][16:] if not bucket_root else None)
        if not selected or selected[0] != 0:
            return ''
        name = selected[1].decode('utf-8')
        return name if name and len(name) <= 4096 and not any(ord(c) < 32 for c in name) else ''
    except (OSError, ValueError, struct.error, UnicodeError):
        return ''


if __name__ == '__main__':
    choice = saved_global(sys.argv[1])
    if not choice:
        sys.exit(1)
    print(choice, end='')
