from __future__ import annotations

import math
import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from spk_chebyshev import Type2Segment, parse_file_record, parse_summary_record, parse_type2_directory


def pack_file(endian):
    data = bytearray(1024)
    data[:8] = b"DAF/SPK "
    struct.pack_into(endian + "ii", data, 8, 2, 6)
    data[16:28] = b"synthetic".ljust(12, b" ")
    struct.pack_into(endian + "iii", data, 76, 3, 0, 50)
    data[88:96] = b"LTL-IEEE" if endian == "<" else b"BIG-IEEE"
    return bytes(data)


def pack_summary(endian):
    data = bytearray(1024)
    struct.pack_into(endian + "ddd", data, 0, 0.0, 0.0, 1.0)
    struct.pack_into(endian + "ddiiiiii", data, 24, 90.0, 130.0, 999, 0, 1, 2, 101, 122)
    return bytes(data)


class SPKChebyshevTests(unittest.TestCase):
    def test_file_and_summary_records_both_endian(self):
        for endian in ("<", ">"):
            file_record = parse_file_record(pack_file(endian))
            self.assertEqual(file_record["endian"], endian)
            self.assertEqual((file_record["ND"], file_record["NI"]), (2, 6))
            self.assertEqual((file_record["forward"], file_record["backward"], file_record["free"]), (3, 0, 50))
            summary = parse_summary_record(pack_summary(endian), endian)
            self.assertEqual((summary["next"], summary["prev"]), (0, 0))
            self.assertEqual(summary["segments"][0]["target"], 999)
            self.assertEqual(summary["segments"][0]["last_address"], 122)

    def test_directory_and_state_first_second_derivatives(self):
        for endian in ("<", ">"):
            directory_bytes = struct.pack(endian + "dddd", 90.0, 20.0, 8.0, 2.0)
            directory = parse_type2_directory(directory_bytes, endian)
            self.assertEqual(directory, (90.0, 20.0, 8, 2))
            # RSIZE=8 => two x, two y, two z coefficients. x=mid+radius*u,
            # y=2*mid+2*radius*u, z=3*mid+3*radius*u, with constant velocity.
            values = (
                100.0, 10.0, 100.0, 10.0, 200.0, 20.0, 300.0, 30.0,
                120.0, 10.0, 120.0, 10.0, 240.0, 20.0, 360.0, 30.0,
            )
            raw = struct.pack(endian + "16d", *values)
            segment = Type2Segment.from_records(
                {"start_et": 90.0, "end_et": 130.0, "target": 999, "center": 0, "frame": 1, "type": 2, "first_address": 1, "last_address": 16},
                raw,
                endian,
                0,
                directory,
            )
            state = segment.state_at_split(100.0, 5.0)
            self.assertEqual(state.position, (105.0, 210.0, 315.0))
            self.assertEqual(state.velocity, (1.0, 2.0, 3.0))
            self.assertEqual(segment.acceleration_at_split(100.0, 5.0), (0.0, 0.0, 0.0))
            # Shared endpoint uses the right-hand SPK interval.
            self.assertEqual(segment.state_at_split(100.0, 20.0).position, (120.0, 240.0, 360.0))
            self.assertEqual(segment.state_at_split(100.0, 30.0).position, (130.0, 260.0, 390.0))

    def test_split_time_avoids_epoch_plus_delta_cancellation(self):
        endian = "<"
        directory = (1_000_000_000.0, 20.0, 8, 1)
        raw = struct.pack(endian + "8d", 1_000_000_010.0, 10.0, 7.0, 2.0, 0.0, 0.0, 0.0, 0.0)
        segment = Type2Segment.from_records(
            {"start_et": 1_000_000_000.0, "end_et": 1_000_000_020.0, "frame": 1, "type": 2}, raw, endian, 0, directory
        )
        state = segment.state_at_split(1_000_000_009.0, 0.5)
        self.assertAlmostEqual(state.position[0], 6.9, delta=1e-14)
        self.assertAlmostEqual(state.velocity[0], 0.2, delta=1e-14)
        with self.assertRaises(ValueError):
            segment.state_at_split(1_000_000_021.0, 0.0)

    def test_subset_records_and_second_derivative(self):
        # A Type 2 record with three coefficients per component: T2''(u)=4.
        directory = (0.0, 20.0, 11, 2)
        first = struct.pack("<11d", 10.0, 10.0, 1.0, 2.0, 3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        second = struct.pack("<11d", 30.0, 10.0, 9.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        segment = Type2Segment.from_records(
            {"start_et": 20.0, "end_et": 40.0, "frame": 1, "type": 2}, second, "<", 1, directory
        )
        self.assertEqual(segment.state_at_split(30.0, 5.0).position, (9.0, 0.0, 0.0))
        self.assertAlmostEqual(segment.state_at_split(30.0, 5.0).velocity[0], 0.0, delta=1e-15)
        self.assertAlmostEqual(segment.acceleration_at_split(30.0, 5.0)[0], 0.0, delta=1e-15)
        full = Type2Segment.from_records(
            {"start_et": 0.0, "end_et": 40.0, "frame": 1, "type": 2}, first + second, "<", 0, directory
        )
        state = full.state_at_split(10.0, 5.0)
        self.assertAlmostEqual(state.position[0], 0.5, delta=1e-15)
        self.assertAlmostEqual(state.velocity[0], 0.8, delta=1e-15)
        self.assertAlmostEqual(full.acceleration_at_split(10.0, 5.0)[0], 0.12, delta=1e-15)

    def test_distant_segment_start_preserves_small_split_delta(self):
        init = -1_000_000_000_000.0
        interval = 1000.0
        first_index = 1_000_900_000
        midpoint = init + (first_index + 0.5) * interval
        count = first_index + 1
        directory = (init, interval, 8, count)
        # x = 1 + u, y=z=0; origin is at midpoint and a microsecond split
        # must remain visible despite the distant segment start.
        raw = struct.pack("<8d", midpoint, interval / 2.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0)
        segment = Type2Segment.from_records(
            {"start_et": init, "end_et": init + count * interval, "frame": 1, "type": 2},
            raw,
            "<",
            first_index,
            directory,
        )
        state = segment.state_at_split(midpoint, 1e-6)
        self.assertGreater(state.position[0], 0.0)
        self.assertAlmostEqual(state.position[0], 1.0 + 2e-9, delta=1e-15)
        self.assertAlmostEqual(state.velocity[0], 2e-3, delta=1e-15)

    def test_unsupported_and_malformed_inputs(self):
        with self.assertRaises(ValueError):
            parse_file_record(b"bad" + bytes(1021))
        malformed_file = bytearray(pack_file("<"))
        malformed_file[88:96] = b"BIG-IEEE"
        with self.assertRaises(ValueError):
            parse_file_record(malformed_file)
        malformed_file = bytearray(pack_file("<"))
        struct.pack_into("<i", malformed_file, 8, 3)
        with self.assertRaises(ValueError):
            parse_file_record(malformed_file)
        with self.assertRaises(ValueError):
            parse_type2_directory(struct.pack("<dddd", 0.0, 1.0, 7.5, 1.0), "<")
        raw = struct.pack("<5d", 0.0, 1.0, 1.0, 0.0, 0.0)
        metadata = {"start_et": -1.0, "end_et": 1.0, "frame": 2, "type": 2}
        with self.assertRaises(ValueError):
            Type2Segment.from_records(metadata, raw, "<", 0, (0.0, 2.0, 5, 1))
        metadata["frame"] = 1
        metadata["type"] = 3
        with self.assertRaises(ValueError):
            Type2Segment.from_records(metadata, raw, "<", 0, (0.0, 2.0, 5, 1))


if __name__ == "__main__":
    unittest.main()
