#!/usr/bin/env python3
"""
XP3 archive extractor for Kirikiri (吉里吉里) engine games.
Extracts .ks scenario scripts from scn.xp3 archives.
"""
import struct
import zlib
import os
import sys


def read_uint32(data, offset):
    return struct.unpack_from('<I', data, offset)[0]


def read_uint64(data, offset):
    return struct.unpack_from('<Q', data, offset)[0]


def extract_xp3(xp3_path, output_dir):
    """Extract files from an XP3 archive."""
    with open(xp3_path, 'rb') as f:
        data = f.read()

    # XP3 magic: "XP3\r\n \n \r\n"
    # But files might also start with a chunk directly
    pos = 0
    file_count = 0

    # Try to read the XP3 header
    magic = data[0:11]
    if magic[:3] == b'XP3':
        pos = 11
        print(f"Found XP3 header at offset 0")

    # Now read chunks until we reach the index
    files_info = []
    while pos < len(data) - 12:
        chunk_type = data[pos:pos+4].decode('ascii', errors='ignore')
        chunk_size = read_uint64(data, pos + 4)
        pos += 12

        if chunk_type == 'File':
            # Read sub-chunks within the File entry
            file_end = pos + chunk_size
            file_info = {'info': None, 'segm': None, 'adlr': None, 'adlr2': None, 'time': None}

            while pos < file_end:
                if pos + 12 > file_end:
                    break
                sub_type = data[pos:pos+4].decode('ascii', errors='ignore')
                sub_size = read_uint64(data, pos + 4)
                sub_data = data[pos + 12:pos + 12 + sub_size]

                if sub_type == 'info':
                    flags = read_uint32(sub_data, 0)
                    file_info['info'] = {'flags': flags}
                elif sub_type == 'segm':
                    flags = read_uint32(sub_data, 0)
                    offset = read_uint64(sub_data, 4)
                    comp_size = read_uint64(sub_data, 12)
                    orig_size = read_uint64(sub_data, 20)
                    file_info['segm'] = {
                        'flags': flags,
                        'offset': offset,
                        'comp_size': comp_size,
                        'orig_size': orig_size,
                    }
                elif sub_type == 'adlr':
                    # Filename in UTF-16LE
                    try:
                        filename = sub_data.decode('utf-16-le', errors='ignore')
                        file_info['adlr'] = filename
                    except:
                        file_info['adlr'] = None
                elif sub_type == 'time':
                    file_info['time'] = read_uint64(sub_data, 0)

                pos += 12 + sub_size

            if file_info['adlr'] and file_info['segm']:
                files_info.append(file_info)
                file_count += 1

            # Move to end of this File chunk
            pos = file_end

        elif chunk_type == 'eliF':
            # End of index marker - read the index
            # Index data: [compressed_size:4][uncompressed_size:4][zlib_data]
            pos += chunk_size
            break  # We're done after eliF

        elif chunk_type == 'nehe':
            # Some archival info chunk
            pos += chunk_size

        else:
            # Unknown chunk type, skip
            pos += chunk_size

    print(f"Found {file_count} file entries")

    # Now extract each file
    os.makedirs(output_dir, exist_ok=True)

    for i, finfo in enumerate(files_info):
        segm = finfo['segm']
        filename = finfo['adlr']
        flags = segm['flags']
        offset = segm['offset']
        comp_size = segm['comp_size']
        orig_size = segm['orig_size']

        # flags & 0x07: 0=uncompressed, 1=zlib, others vary
        compression = flags & 0x07

        raw_data = data[offset:offset + comp_size]

        try:
            if compression == 0 and comp_size == orig_size:
                # Uncompressed
                file_data = raw_data
            elif compression == 1:
                # zlib compressed (possibly with stripped header)
                try:
                    # Try standard zlib decompress first
                    file_data = zlib.decompress(raw_data)
                except zlib.error:
                    # Try raw deflate (no 2-byte zlib header)
                    file_data = zlib.decompress(raw_data, -15)
            elif compression == 3:
                # zlib without header (raw deflate)
                file_data = zlib.decompress(raw_data, -15)
            else:
                print(f"  [{i}] Unknown compression {compression} for {filename}, skipping")
                continue

            # Write file
            out_path = os.path.join(output_dir, filename)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, 'wb') as fout:
                fout.write(file_data)

            if i < 20 or i % 50 == 0:
                print(f"  [{i}] Extracted: {filename} ({len(file_data)} bytes)")

        except Exception as e:
            print(f"  [{i}] Error extracting {filename}: {e}")

    print(f"\nExtracted {file_count} files to {output_dir}")
    return output_dir


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python xp3_extract.py <xp3_file> [output_dir]")
        sys.exit(1)

    xp3_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.splitext(xp3_path)[0] + '_extracted'

    extract_xp3(xp3_path, output_dir)
