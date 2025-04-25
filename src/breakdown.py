import json
import yaml
import shutil
from pathlib import Path
from google import genai
from google.genai.types import (
    GenerateContentConfig,
    HarmBlockThreshold,
    HarmCategory,
    Part,
    SafetySetting
)
from pdf_compressor import compress_pdf
import argparse
from loguru import logger
from logging_config import configure_logging

# Configure logger
logger = configure_logging()


def load_config(config_path="config.yaml"):
    """Load configuration from config file."""
    with open(config_path, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    return config


def setup_genai_api(api_key):
    """Setup Google Generative AI API with the provided key."""
    return genai.Client(api_key=api_key)


def preprocess_pdf(input_pdf, output_dir):
    """
    Check if PDF is too large and compress it if necessary.
    Returns the path to the PDF that should be used.
    """
    # Create output directory if it doesn't exist
    output_dir.mkdir(parents=True, exist_ok=True)

    # Define paths
    processed_pdf = output_dir / "input.pdf"
    original_pdf = output_dir / "input_original.pdf"

    # If processed file already exists and original is backed up, just return the processed file
    if processed_pdf.exists() and original_pdf.exists():
        return processed_pdf

    # Copy the input file if needed (first time processing)
    if not processed_pdf.exists():
        shutil.copy2(input_pdf, processed_pdf)

    # Get file size in MB
    file_size_mb = processed_pdf.stat().st_size / (1024 * 1024)

    # If file size > 45MB, compress it
    if file_size_mb > 45:
        logger.warning(f"PDF file size ({file_size_mb:.2f}MB) exceeds 45MB. Compressing...")

        # Backup original PDF if not already done
        if not original_pdf.exists():
            shutil.copy2(processed_pdf, original_pdf)

        # Create temporary file for compression output
        temp_output = output_dir / "compressed_temp.pdf"

        # Start with moderate compression settings
        compression_settings = [
            # (dpi, quality, grayscale)
            (150, 60, False),  # Medium compression
            (120, 40, False),  # Higher compression
            (100, 30, True)    # Aggressive compression with grayscale
        ]

        # Try compression with increasingly aggressive settings until size is under limit
        for dpi, quality, grayscale in compression_settings:
            try:
                logger.info(f"Trying compression with DPI={dpi}, quality={quality}, grayscale={grayscale}...")
                success, stats = compress_pdf(
                    str(processed_pdf), 
                    str(temp_output), 
                    dpi=dpi, 
                    quality=quality, 
                    grayscale=grayscale
                )

                if success:
                    compressed_size_mb = stats["output_size_mb"]
                    logger.info(
                        f"Compression result: {compressed_size_mb:.2f}MB ({stats['saved_percentage']:.1f}% reduction)"
                    )

                    # If compression was successful and reduced size, use the compressed file
                    if compressed_size_mb < file_size_mb:
                        # Replace the processed file with our compressed version
                        if temp_output.exists():
                            shutil.move(str(temp_output), str(processed_pdf))
                    else:
                        logger.warning("Compression did not reduce file size. Keeping original.")

                    # If we're under 45MB, we're done
                    if compressed_size_mb <= 45:
                        break

            except Exception as e:
                logger.error(f"Compression attempt failed: {e}")

            # Clean up temp file if it exists
            if temp_output.exists():
                temp_output.unlink()

        # Check final file size
        final_size_mb = processed_pdf.stat().st_size / (1024 * 1024)
        if final_size_mb > 45:
            logger.warning(f"PDF is still {final_size_mb:.2f}MB (larger than 45MB) after compression")

    return processed_pdf


def analyze_pdf_structure(client: genai.Client, pdf_path, book_title, config):
    """Use Gemini model to analyze the PDF structure from the full PDF."""
    prompt = f"""
    Analyze this book PDF with title "{book_title}" and provide a detailed breakdown of its structure.
    Include the following elements:
    1. Cover page (page number)
    2. Table of contents (page numbers)
    3. All chapters and subchapters as referenced in the table of contents
    4. Back cover page (page number)
    
    Important: Use the PDF page numbers (not the printed page numbers that might appear in the table of contents).
    Note that nearby chapters may overlap if there are no page breaks.
    Keep the original language for all titles.
    
    Return the result in the following JSON structure:
    {{
        "cover_page": {{
            "page_number": int
        }},
        "table_of_contents": {{
            "start_page": int,
            "end_page": int,
            "entries": [
                {{
                    "title": string,
                    "page_number": int,
                    "level": int  # 1 for main chapter, 2 for subchapter, etc.
                }}
            ]
        }},
        "chapters": [
            {{
                "title": string,
                "start_page": int,
                "end_page": int,
                "level": int,
                "subchapters": [
                    {{
                        "title": string,
                        "start_page": int,
                        "end_page": int,
                        "level": int
                    }}
                ]
            }}
        ],
        "back_cover": {{
            "page_number": int
        }}
    }}
    """

    # Read the PDF file as binary
    with open(pdf_path, "rb") as f:
        pdf_data = f.read()

    # Create parts for the multimodal input - text and PDF data
    parts = [
        prompt,
        Part.from_bytes(data=pdf_data, mime_type="application/pdf"),
    ]

    # Get model from config with fallback
    model = config.get("model", "gemini-2.5-pro-preview-03-25")
    
    # Get number of retries from config
    num_retries = config.get("num_retries", 3)
    retry_count = 0
    response = None
    
    while response is None and retry_count < num_retries:
        if retry_count > 0:
            logger.warning(f"Retry attempt {retry_count} for PDF structure analysis...")
            
        try:
            # Generate content with structured response
            response = client.models.generate_content(
                model=model,
                contents=parts,
                config=GenerateContentConfig(
                    temperature=0.1,
                    response_mime_type="application/json",
                    safety_settings=[
                        SafetySetting(
                            category=HarmCategory.HARM_CATEGORY_HARASSMENT,
                            threshold=HarmBlockThreshold.BLOCK_NONE,
                        ),
                        SafetySetting(
                            category=HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                            threshold=HarmBlockThreshold.BLOCK_NONE,
                        ),
                        SafetySetting(
                            category=HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                            threshold=HarmBlockThreshold.BLOCK_NONE,
                        ),
                        SafetySetting(
                            category=HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                            threshold=HarmBlockThreshold.BLOCK_NONE,
                        ),
                    ],
                )
            )
        except Exception as e:
            logger.error(f"API call failed: {e}")
            response = None
        
        retry_count += 1
    
    if response is None:
        raise ValueError(f"Failed to analyze PDF structure after {num_retries} attempts")
    
    logger.debug(f"API response: {response}")

    # Parse the response as JSON
    return json.loads(response.text)


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description='Process a PDF book for structure analysis.')
    parser.add_argument('-i', '--input', required=True, help='Path to input PDF file')
    parser.add_argument('-c', '--config', default='config.yaml', help='Path to config file (default: config.yaml)')
    args = parser.parse_args()
    
    # Load configuration
    config = load_config(args.config)
    api_key = config.get("google_api_key")
    
    # Get input PDF path
    input_pdf = Path(args.input)
    
    # Get book title from config instead of PDF filename
    book_title = config.get("title")
    if not book_title:
        # Fallback to PDF filename if title not in config
        book_title = input_pdf.stem
        logger.warning(f"No title found in config, using PDF filename: {book_title}")
    
    # Define output directory based on config title
    output_dir = Path("output") / Path(book_title)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check if API key exists
    if not api_key:
        raise ValueError("Google API key not found in config.yaml")

    # Setup Gemini API
    client = setup_genai_api(api_key)

    # Preprocess and get the PDF path to use
    processed_pdf = preprocess_pdf(input_pdf, output_dir)
    
    # Analyze PDF structure using Gemini
    logger.info(f"Analyzing PDF structure for '{book_title}'...")
    try:
        structure = analyze_pdf_structure(client, processed_pdf, book_title, config)

        # Save the structured output to the output directory
        output_file = output_dir / "book_structure.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(structure, f, ensure_ascii=False, indent=2)

        logger.success(f"Book structure analysis completed and saved to {output_file}")

        # Print a summary
        logger.info("\nStructure Summary:")
        logger.info(
            f"Cover page: {structure.get('cover_page', {}).get('page_number', 'Not found')}"
        )
        logger.info(
            f"Table of contents: Pages {structure.get('table_of_contents', {}).get('start_page', 'N/A')}-"
            f"{structure.get('table_of_contents', {}).get('end_page', 'N/A')}"
        )
        logger.info(f"Total chapters: {len(structure.get('chapters', []))}")
        logger.info(
            f"Back cover: {structure.get('back_cover', {}).get('page_number', 'Not found')}"
        )

    except Exception as e:
        logger.error(f"Error analyzing PDF structure: {e}")
        raise


if __name__ == "__main__":
    main()
