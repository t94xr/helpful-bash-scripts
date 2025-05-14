# AV1 Encoding Script (av1_enc.py)

**Licence:** MIT Licence

### Features
- **Automated AV1 Encoding:** Recursively scans your specified directory (and subfolders) for common video files and automatically converts them to the efficient AV1 codec.
- **Multi-threaded Processing:** Leverages multi-core processors by encoding up to 4 videos in parallel, significantly speeding up the overall conversion process.
- **AV1 Skip Logic:** Intelligently detects files that are already encoded in AV1 and skips them, preventing unnecessary re-encoding.
- **In-Place Replacement:** After successful encoding, the original video file in its source folder is replaced with the newly created AV1 file (with the .mkv extension).
- **Real-time Colored Output:** Provides clear and informative feedback directly in your terminal, using colors to distinguish between successful operations (green), errors (red), and skipped files (yellow).
- **Detailed Logging:** Records all processing steps, including file sizes and compression ratios, in a log file for later review.
- **FFmpeg with QSV Acceleration:** Designed to utilize Intel's Quick Sync Video (QSV) for hardware-accelerated encoding, potentially leading to faster processing on compatible systems.
- **Handles Common Video Formats:** Supports a wide range of input video formats including .mp4, .mkv, .avi, .mov, .wmv, .flv, and .webm.
- **Size Reduction Reporting:** Displays the percentage of size reduction achieved after encoding, allowing you to see the benefits of AV1 compression.

