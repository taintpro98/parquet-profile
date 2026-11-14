package com.mbbank.archive.common;

import java.io.IOException;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.channels.FileChannel;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

/**
 * A growable byte buffer with the encodings the custom file formats share with Go: little-endian
 * longs and unsigned varints (the same as Go's encoding/binary Uvarint).
 */
public final class Bytes {

    private byte[] data;
    private int size;
    private int position; // read position

    public Bytes(int capacity) {
        this.data = new byte[capacity];
    }

    public static Bytes wrap(byte[] data, int size) {
        Bytes bytes = new Bytes(0);
        bytes.data = data;
        bytes.size = size;
        return bytes;
    }

    /** Reads length bytes at offset of the channel. */
    public static byte[] read(FileChannel channel, long offset, int length) throws IOException {
        ByteBuffer buffer = ByteBuffer.allocate(length);
        while (buffer.hasRemaining()) {
            if (channel.read(buffer, offset + buffer.position()) < 0) {
                throw new IOException("Unexpected end of file");
            }
        }
        return buffer.array();
    }

    public byte[] array() {
        return data;
    }

    public int size() {
        return size;
    }

    public void clear() {
        size = 0;
        position = 0;
    }

    private void ensure(int extra) {
        if (size + extra > data.length) {
            data = Arrays.copyOf(data, Math.max(data.length * 2, size + extra));
        }
    }

    public void putLong(long value) {
        ensure(8);
        ByteBuffer.wrap(data, size, 8).order(ByteOrder.LITTLE_ENDIAN).putLong(value);
        size += 8;
    }

    public void putUvarint(long value) {
        ensure(10);
        while ((value & ~0x7FL) != 0) {
            data[size++] = (byte) ((value & 0x7F) | 0x80);
            value >>>= 7;
        }
        data[size++] = (byte) value;
    }

    public void put(byte[] bytes) {
        ensure(bytes.length);
        System.arraycopy(bytes, 0, data, size, bytes.length);
        size += bytes.length;
    }

    public void putString(String value) {
        byte[] utf8 = value.getBytes(StandardCharsets.UTF_8);
        putUvarint(utf8.length);
        put(utf8);
    }

    public boolean hasRemaining() {
        return position < size;
    }

    public int position() {
        return position;
    }

    public void skip(int length) {
        position += length;
    }

    public long getLong() {
        long value = ByteBuffer.wrap(data, position, 8).order(ByteOrder.LITTLE_ENDIAN).getLong();
        position += 8;
        return value;
    }

    public long getUvarint() {
        long value = 0;
        for (int shift = 0; ; shift += 7) {
            byte b = data[position++];
            value |= (long) (b & 0x7F) << shift;
            if (b >= 0) {
                return value;
            }
        }
    }

    public String getString(int length) {
        String value = new String(data, position, length, StandardCharsets.UTF_8);
        position += length;
        return value;
    }

    public String getString() {
        return getString((int) getUvarint());
    }
}
