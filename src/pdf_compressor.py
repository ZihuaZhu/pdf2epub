#!/usr/bin/env python3
"""
pdf_compressor.py - Standalone PDF compression utility

Compresses PDF files by:
1. Converting each page to an image
2. Applying JPEG compression with customizable quality
3. Creating a new PDF from the compressed images

Usage:
  python pdf_compressor.py input.pdf output.pdf --dpi 150 --quality 60 --grayscale
"""

import os
import sys
import argparse
import tempfile
from pathlib import Path
from PIL import Image
import fitz  # PyMuPDF


def compress_pdf(input_path, output_path, dpi=150, quality=60, grayscale=False):
    """
    Compress a PDF by flattening each page to a JPEG image with specified quality.

    Args:
        input_path (str): Path to input PDF file
        output_path (str): Path to save compressed PDF
        dpi (int): Resolution for rendering PDF pages (higher = better quality but larger size)
        quality (int): JPEG compression quality (0-100, lower = smaller size but lower quality)
        grayscale (bool): Whether to convert to grayscale for additional compression

    Returns:
        tuple: (bool success, dict stats)
    """
    try:
        input_size = os.path.getsize(input_path)

        # Open the PDF
        pdf_document = fitz.open(input_path)
        page_count = len(pdf_document)

        # Create a new PDF for output
        output_pdf = fitz.open()

        # Calculate zoom factor based on DPI (72 is the base DPI)
        zoom = dpi / 72

        # Set up temp directory for page images
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_dir_path = Path(temp_dir)

            print(f"Processing {page_count} pages...")

            # Process each page
            for page_num, page in enumerate(pdf_document):
                sys.stdout.write(f"\rConverting page {page_num + 1}/{page_count}")
                sys.stdout.flush()

                # Get page dimensions
                rect = page.rect

                # Convert page to image
                pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)

                # Convert to PIL Image
                img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

                # Optional grayscale conversion
                if grayscale:
                    img = img.convert("L")

                # Save as JPEG with specified quality
                img_path = temp_dir_path / f"page_{page_num + 1}.jpg"
                img.save(img_path, "JPEG", quality=quality, optimize=True)

                # Add image back to new PDF
                new_page = output_pdf.new_page(width=rect.width, height=rect.height)
                new_page.insert_image(rect, filename=str(img_path))

            print("\nSaving compressed PDF...")
            output_pdf.save(output_path, garbage=4, deflate=True, clean=True)
            output_pdf.close()

        # Get stats
        output_size = os.path.getsize(output_path)
        compression_ratio = input_size / output_size if output_size > 0 else 0
        saved_percentage = (1 - output_size / input_size) * 100

        stats = {
            "input_size_mb": input_size / 1024 / 1024,
            "output_size_mb": output_size / 1024 / 1024,
            "compression_ratio": compression_ratio,
            "saved_percentage": saved_percentage,
            "page_count": page_count,
        }

        print(f"Original size: {stats['input_size_mb']:.2f} MB")
        print(f"Compressed size: {stats['output_size_mb']:.2f} MB")
        print(f"Compression ratio: {stats['compression_ratio']:.2f}x")
        print(f"Space saved: {stats['saved_percentage']:.2f}%")

        return True, stats

    except Exception as e:
        print(f"Error compressing PDF: {e}")
        return False, {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="Compress PDF by flattening to images with JPEG compression"
    )
    parser.add_argument("input", help="Input PDF file path")
    parser.add_argument("output", help="Output PDF file path")
    parser.add_argument(
        "--dpi", type=int, default=150, help="DPI for rendering (default: 150)"
    )
    parser.add_argument(
        "--quality", type=int, default=60, help="JPEG quality 0-100 (default: 60)"
    )
    parser.add_argument(
        "--grayscale",
        action="store_true",
        help="Convert to grayscale for additional compression",
    )

    args = parser.parse_args()

    # Validate input file
    if not os.path.isfile(args.input):
        print(f"Error: Input file '{args.input}' not found")
        return 1

    # Validate quality range
    if args.quality < 0 or args.quality > 100:
        print("Error: Quality must be between 0 and 100")
        return 1

    print(f"Compressing {args.input} to {args.output}")
    print(
        f"Settings: DPI={args.dpi}, Quality={args.quality}, Grayscale={args.grayscale}"
    )

    success, _ = compress_pdf(
        args.input, args.output, args.dpi, args.quality, args.grayscale
    )
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
