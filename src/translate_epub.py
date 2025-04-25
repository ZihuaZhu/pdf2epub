import os
import shutil
import yaml
import zipfile
import re
import hashlib
import json
from pathlib import Path
from google import genai
from google.genai.types import (
    GenerateContentConfig,
    HarmBlockThreshold,
    HarmCategory,
    SafetySetting,
    Part,
)
from utils.network_utils import generate_content_with_retry, get_default_generation_config
import xml.etree.ElementTree as ET
import argparse
from loguru import logger
from utils.logging_config import configure_logging

# Configure logger
logger = configure_logging()


def load_config():
    """Load configuration from config.yaml file."""
    with open("config.yaml", "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    return config


def setup_genai_api(api_key):
    """Setup Google Generative AI API with the provided key."""
    from utils.network_utils import setup_genai_client
    return setup_genai_client(api_key)


def ensure_directory(directory_path):
    """Ensure a directory exists, create it if it doesn't."""
    Path(directory_path).mkdir(parents=True, exist_ok=True)


def clean_html_response(html_content):
    """Clean the HTML response from Gemini."""
    if html_content is None:
        return None
    
    try:
        # First try to parse as JSON in case response was JSON-encoded
        try:
            parsed = json.loads(html_content)
            if isinstance(parsed, str):
                html_content = parsed
            elif isinstance(parsed, dict) and "html" in parsed:
                html_content = parsed["html"]
        except json.JSONDecodeError:
            pass  # Not JSON, proceed with normal cleaning
            
        # Clean up markdown code blocks
        html_content = re.sub(r"```html\s*", "", html_content)
        html_content = re.sub(r"```\s*$", "", html_content)
        html_content = re.sub(r"```[a-zA-Z]*\s*", "", html_content)
        
        # Extract HTML content
        # Try to match full HTML document first
        html_match = re.search(
            r"(?:<\!DOCTYPE.*?>)?(?:<html.*?>).*?<\/html>", html_content, re.DOTALL
        )
        
        # If no match, try to match just the body content
        if not html_match:
            html_match = re.search(
                r"(?:<body.*?>).*?<\/body>", html_content, re.DOTALL
            )
            
        # If still no match, try to match any HTML-like content between div tags
        if not html_match:
            html_match = re.search(
                r"<div.*?>.*?<\/div>", html_content, re.DOTALL
            )
            
        if not html_match:
            # If we can't find valid HTML, log the raw response for debugging
            logger.error("Failed to find valid HTML structure in response")
            logger.debug("Response content structure:")
            logger.debug("-" * 40)
            logger.debug(f"Content starts with: {html_content[:200]}")
            logger.debug("..." if len(html_content) > 200 else "(end)")
            logger.debug("-" * 40)
            raise ValueError("Could not find valid HTML content in response")
            
        if html_match:
            html_content = html_match.group(0)
            
        # Final cleanup
        html_content = html_content.strip()
        if not html_content:
            raise ValueError("Cleaned HTML content is empty")
            
        return html_content
        
    except Exception as e:
        logger.error(f"Error cleaning HTML response: {str(e)}")
        logger.debug(f"Original content: {html_content[:500]}...")  # Log first 500 chars for debugging
        raise ValueError("Failed to clean HTML response") from e


def extract_epub(epub_path, extract_dir):
    """Extract EPUB contents to a directory."""
    with zipfile.ZipFile(epub_path, "r") as zip_ref:
        zip_ref.extractall(extract_dir)
    logger.info(f"Extracted EPUB to {extract_dir}")


def parse_toc_ncx(ncx_path):
    """Parse the toc.ncx file to get chapter structure."""
    tree = ET.parse(ncx_path)
    root = tree.getroot()

    # Extract namespace from root tag if it exists
    namespace = ""
    if "}" in root.tag:
        namespace = root.tag.split("}")[0].strip("{")

    # Define namespace dictionary for finding elements
    ns = {"ncx": namespace} if namespace else {}

    chapters = []

    # Find all navPoint elements (chapters) at any level
    nav_points_xpath = ".//ncx:navPoint" if ns else ".//navPoint"
    for nav_point in root.findall(nav_points_xpath, ns):
        # Get chapter title
        if ns:
            nav_label = nav_point.find(".//ncx:navLabel", ns)
            title_elem = (
                nav_label.find("ncx:text", ns) if nav_label is not None else None
            )
        else:
            nav_label = nav_point.find(".//navLabel")
            title_elem = nav_label.find("text") if nav_label is not None else None

        title = title_elem.text if title_elem is not None else "Unknown Title"

        # Get chapter source file
        if ns:
            content_elem = nav_point.find("./ncx:content", ns)
            if content_elem is None:
                content_elem = nav_point.find(".//ncx:content", ns)
        else:
            content_elem = nav_point.find("./content")
            if content_elem is None:
                content_elem = nav_point.find(".//content")

        if content_elem is not None:
            src = content_elem.get("src")
            chapters.append({"title": title, "src": src})

    return chapters


def find_all_html_files(epub_extract_dir):
    """Find all HTML files in the EPUB directory."""
    all_html_files = []
    for path in Path(epub_extract_dir).rglob("*.htm*"):  # Match both .html and .htm
        if path.is_file():
            # Get the relative path from the extract dir
            rel_path = path.relative_to(epub_extract_dir).as_posix()
            all_html_files.append(str(rel_path))

    return all_html_files


def translate_html_content(
    html_content,
    chapter_title,
    book_title,
    source_language,
    target_language,
    client,
    config,
    previous_content=None,
):
    """Translate HTML content using Gemini API."""
    # Get previous_content_limit from config
    previous_content_limit = config.get("previous_content_limit", 0)
    
    context = ""
    if previous_content and previous_content_limit > 0:
        context = f"Previous chapter content (for context only, do not translate this again):\n{previous_content[:previous_content_limit]}\n\n"

    # Create instruction prompt
    prompt = f"""
    Translate the HTML content provided in the multipart input from {source_language} to {target_language}.
    
    Book title: {book_title}
    Chapter title: {chapter_title}
    
    STRICT RESPONSE FORMAT REQUIREMENTS:
    - Your response must begin directly with <!DOCTYPE html> or <html> tag
    - Return only raw HTML text, not formatted as JSON or markdown
    - Do not include any explanations or commentary before or after the HTML
    - Do not wrap the HTML in code blocks or quotes
    
    TRANSLATION REQUIREMENTS:
    1. Preserve all HTML tags, attributes, and structure exactly as they are
    2. Only translate the text content inside tags, not the tags themselves
    3. Preserve all class names, IDs, and other attributes
    4. Keep all image references and links intact
    5. Maintain the same formatting and structure 
    6. Translate all visible text, including image alt attributes, but don't change any code
    7. Make sure the translation is accurate and natural-sounding in {target_language}
    8. For names of people, places, or titles that have standard translations in {target_language},
       use those standard translations
    
    {context}Return only the translated HTML as raw text, without any JSON formatting, markdown code blocks, or other commentary.
    The response should start directly with the HTML content.
    """

    # Create multipart input with instruction and HTML content
    parts = [
        prompt,
        html_content
    ]

    # Get model from config with fallback
    model = config.get("model", "gemini-2.5-pro-preview-03-25")
    
    # Get number of retries from config
    num_retries = config.get("num_retries", 3)
    max_backoff = config.get("max_backoff_seconds", 30)
    
    # Get generation config with slightly higher temperature for translation
    generation_config = get_default_generation_config(temperature=0.2)
    
    # Generate content with retry using multipart input
    response = generate_content_with_retry(
        client=client,
        model=model,
        contents=parts,
        config=generation_config,
        max_retries=num_retries,
        max_backoff=max_backoff,
        operation_name=f"HTML translation for {chapter_title}",
        use_streaming=True
    )
    
    # Clean and return the translated HTML
    translated_html = clean_html_response(response.text)
    
    if translated_html is None:
        raise ValueError(f"Failed to translate HTML content for {chapter_title}")
        
    return translated_html


def translate_book_title(book_title, source_language, target_language, client, config):
    """Translate the book title using Gemini API."""
    prompt = f"""
    Translate the following book title from {source_language} to {target_language}.
    Only return the translated title without any explanations or additional text.
    
    Book title: {book_title}
    """

    # Get model from config with fallback
    model = config.get("model", "gemini-2.5-pro-preview-03-25")
    
    # Get number of retries from config
    num_retries = config.get("num_retries", 3)
    max_backoff = config.get("max_backoff_seconds", 30)
    
    # Get generation config
    generation_config = get_default_generation_config(temperature=0.1)
    
    # Generate content with retry
    response = generate_content_with_retry(
        client=client,
        model=model,
        contents=prompt,
        config=generation_config,
        max_retries=num_retries,
        max_backoff=max_backoff,
        operation_name="Book title translation",
        use_streaming=True
    )

    return response.text.strip()


def translate_toc_entries(chapters, source_language, target_language, client, config):
    """Translate the table of contents entries."""
    translated_chapters = []

    # Batch process chapters to reduce API calls
    batch_size = 10
    for i in range(0, len(chapters), batch_size):
        batch = chapters[i : i + batch_size]

        titles = [chapter["title"] for chapter in batch]
        titles_str = "\n".join([f"{j + 1}. {title}" for j, title in enumerate(titles)])

        prompt = f"""
        Translate the following chapter titles from {source_language} to {target_language}.
        Return only the translated titles, one per line, numbered as in the original list.
        
        {titles_str}
        """

        # Get model from config with fallback
        model = config.get("model", "gemini-2.5-pro-preview-03-25")
        
        # Get number of retries from config
        num_retries = config.get("num_retries", 3)
        max_backoff = config.get("max_backoff_seconds", 30)
        
        # Get generation config
        generation_config = get_default_generation_config(temperature=0.1)
        
        # Generate content with retry
        response = generate_content_with_retry(
            client=client,
            model=model,
            contents=prompt,
            config=generation_config,
            max_retries=num_retries,
            max_backoff=max_backoff,
            operation_name=f"TOC entries translation (batch {i // batch_size + 1})",
            use_streaming=True
        )

        translated_titles = response.text.strip().split("\n")
        cleaned_titles = [
            re.sub(r"^\d+\.\s*", "", title).strip() for title in translated_titles
        ]

        for j, chapter in enumerate(batch):
            if j < len(cleaned_titles):
                translated_chapters.append(
                    {
                        "title": cleaned_titles[j],
                        "src": chapter["src"],
                        "original_title": chapter["title"],
                    }
                )
            else:
                # Fallback if translation failed
                translated_chapters.append(
                    {
                        "title": chapter["title"],
                        "src": chapter["src"],
                        "original_title": chapter["title"],
                    }
                )

    return translated_chapters


def update_toc_ncx(toc_path, translated_chapters):
    """Update the toc.ncx file with translated chapter titles."""
    tree = ET.parse(toc_path)
    root = tree.getroot()

    # Extract namespace from root tag if it exists
    namespace = ""
    if "}" in root.tag:
        namespace = root.tag.split("}")[0] + "}"

    # Define namespace dictionary for finding elements
    ns = {"ncx": namespace.strip("{}")} if namespace else {}

    # Update each navPoint with the translated title
    nav_points_xpath = ".//ncx:navPoint" if ns else ".//navPoint"
    for nav_point in root.findall(nav_points_xpath, ns):
        # Get content element to find src
        content_elem_xpath = "./ncx:content" if ns else "./content"
        content_elem = nav_point.find(content_elem_xpath, ns)

        if content_elem is not None:
            src = content_elem.get("src")

            # Find matching chapter
            for chapter in translated_chapters:
                # Account for variations in path representation
                if Path(chapter["src"]).name == Path(src).name:
                    # Update the title
                    text_elem_xpath = ".//ncx:text" if ns else ".//text"
                    title_elem = nav_point.find(text_elem_xpath, ns)
                    if title_elem is not None:
                        title_elem.text = chapter["title"]
                    break

    # Write updated toc.ncx
    tree.write(toc_path, encoding="utf-8", xml_declaration=True)


def calculate_file_hash(file_path):
    """Calculate MD5 hash of a file for comparison."""
    hasher = hashlib.md5()
    with open(file_path, "rb") as file:
        buf = file.read()
        hasher.update(buf)
    return hasher.hexdigest()


def save_translation_progress(progress_file, progress_data):
    """Save translation progress to a JSON file."""
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(progress_data, f, indent=2)


def load_translation_progress(progress_file):
    """Load translation progress from a JSON file."""
    if Path(progress_file).exists():
        with open(progress_file, "r", encoding="utf-8") as f:
            return json.load(f)
    return {
        "book_title_translated": False,
        "toc_translated": False,
        "content_opf_updated": False,
        "translated_chapters": [],
        "translated_html_files": [],
        "translated_book_title": "",
        "last_processed_chapter_index": -1,
        "last_processed_html_index": -1,
    }


def translate_epub(input_epub_path, source_language, target_language, config):
    """Main function to translate an EPUB file."""
    # Setup paths and directories
    input_file_name = Path(input_epub_path).stem
    
    # Get book title from config instead of input filename
    original_book_title = config.get("title")
    if not original_book_title:
        # Fallback to input filename if title not in config
        original_book_title = input_file_name
        logger.warning(f"No title found in config, using input filename: {original_book_title}")

    # Setup API
    api_key = config.get("google_api_key")
    if not api_key:
        raise ValueError("Google API key not found in config.yaml")
    client = setup_genai_api(api_key)

    # Setup directories
    output_dir = Path("output") / Path(original_book_title)
    epub_extract_dir = output_dir / "extract"
    epub_translated_dir = output_dir / "translated"
    progress_file = output_dir / "translation_progress.json"

    ensure_directory(output_dir)

    # Check if we're resuming a previous translation
    resuming = epub_translated_dir.exists()
    progress = None

    if resuming:
        logger.info("Found existing translation. Attempting to resume...")
        progress = load_translation_progress(progress_file)
    else:
        # Start fresh
        ensure_directory(epub_extract_dir)
        ensure_directory(epub_translated_dir)

        # Extract the EPUB
        extract_epub(input_epub_path, epub_extract_dir)

        # Initialize progress tracking
        progress = {
            "book_title_translated": False,
            "toc_translated": False,
            "content_opf_updated": False,
            "translated_chapters": [],
            "translated_html_files": [],
            "translated_book_title": "",
            "last_processed_chapter_index": -1,
            "last_processed_html_index": -1,
        }

    # Find toc.ncx
    toc_ncx_path = None
    for path in Path(epub_extract_dir).rglob("toc.ncx"):
        toc_ncx_path = path
        break

    if not toc_ncx_path:
        raise FileNotFoundError("toc.ncx not found in the EPUB file.")

    # Parse toc.ncx to get chapter structure
    chapters = parse_toc_ncx(toc_ncx_path)

    # Check if target_title is provided in config, otherwise translate the book title
    if not progress["book_title_translated"]:
        if "target_title" in config:
            translated_book_title = config["target_title"]
            logger.info(f"Original title: {original_book_title}")
            logger.info(f"Using target title from config: {translated_book_title}")
        else:
            translated_book_title = translate_book_title(
                original_book_title, source_language, target_language, client, config
            )
            logger.info(f"Original title: {original_book_title}")
            logger.info(f"Translated title: {translated_book_title}")
        
        progress["translated_book_title"] = translated_book_title
        progress["book_title_translated"] = True
        save_translation_progress(progress_file, progress)
    else:
        translated_book_title = progress["translated_book_title"]
        logger.info(f"Using existing translated title: {translated_book_title}")

    # Translate chapter titles if not already done
    if not progress["toc_translated"]:
        translated_chapters = translate_toc_entries(
            chapters, source_language, target_language, client, config
        )
        progress["translated_chapters"] = [
            {
                "title": chapter["title"],
                "src": chapter["src"],
                "original_title": chapter["original_title"],
                "translated": False,
            }
            for chapter in translated_chapters
        ]
        progress["toc_translated"] = True
        save_translation_progress(progress_file, progress)
    else:
        # Use existing translated chapter titles
        logger.info("Using existing translated chapter titles")
        translated_chapters = progress["translated_chapters"]

    # Find all HTML files
    all_html_files = find_all_html_files(epub_extract_dir)

    # Remove chapter files from all_html_files to avoid duplication
    chapter_files = set(Path(chapter["src"]).name for chapter in chapters)
    non_chapter_html_files = [
        html_file
        for html_file in all_html_files
        if Path(html_file).name not in chapter_files
    ]

    logger.info(
        f"Found {len(chapters)} chapters in TOC and {len(non_chapter_html_files)} additional HTML files"
    )

    # Initialize translated_html_files in progress if not already done
    if "translated_html_files" not in progress:
        progress["translated_html_files"] = []

    # Add any previously untracked HTML files to progress
    tracked_html_files = set(item["src"] for item in progress["translated_html_files"])
    for html_file in non_chapter_html_files:
        if html_file not in tracked_html_files:
            progress["translated_html_files"].append(
                {
                    "src": html_file,
                    "translated": False,
                    "title": f"Additional content: {Path(html_file).name}",
                }
            )
    save_translation_progress(progress_file, progress)

    # Copy all files to the translated directory first (if not already done)
    if not resuming:
        shutil.copytree(epub_extract_dir, epub_translated_dir, dirs_exist_ok=True)

    # Update toc.ncx with translated titles (if not already done)
    translated_toc_path = Path(epub_translated_dir) / toc_ncx_path.relative_to(
        epub_extract_dir
    )
    if not resuming or not progress["toc_translated"]:
        update_toc_ncx(translated_toc_path, translated_chapters)

    # Update content.opf to update book title (if not already done)
    if not progress["content_opf_updated"]:
        content_opf_paths = list(Path(epub_translated_dir).rglob("content.opf"))
        if content_opf_paths:
            content_opf_path = content_opf_paths[0]
            with open(content_opf_path, "r", encoding="utf-8") as f:
                content_opf_content = f.read()

            # Update title in content.opf
            content_opf_content = re.sub(
                r"<dc:title>.*?</dc:title>",
                f"<dc:title>{translated_book_title}</dc:title>",
                content_opf_content,
            )

            with open(content_opf_path, "w", encoding="utf-8") as f:
                f.write(content_opf_content)

            progress["content_opf_updated"] = True
            save_translation_progress(progress_file, progress)

    # Translate chapters that haven't been translated yet
    previous_content = None
    start_index = progress["last_processed_chapter_index"] + 1

    for i, chapter in enumerate(translated_chapters[start_index:], start=start_index):
        if progress["translated_chapters"][i].get("translated", False):
            logger.info(
                f"Skipping already translated chapter {i + 1}/{len(translated_chapters)}: {chapter['title']}"
            )
            continue

        chapter_path = None
        chapter_src = chapter["src"]

        # Find the full path to the chapter file
        for path in Path(epub_translated_dir).rglob(Path(chapter_src).name):
            chapter_path = path
            break

        if chapter_path:
            logger.info(
                f"Translating chapter {i + 1}/{len(translated_chapters)}: {chapter['original_title']} → {chapter['title']}"
            )

            # Read the HTML content
            with open(chapter_path, "r", encoding="utf-8") as f:
                html_content = f.read()

            # Translate the HTML content
            translated_html = translate_html_content(
                html_content,
                chapter["title"],
                translated_book_title,
                source_language,
                target_language,
                client,
                config,
                previous_content,
            )

            # Save the translated HTML
            with open(chapter_path, "w", encoding="utf-8") as f:
                f.write(translated_html)

            # Store for context in next translation based on config limit
            previous_content_limit = config.get("previous_content_limit", 0)
            if previous_content_limit > 0:
                previous_content = translated_html[:previous_content_limit]
            else:
                previous_content = None

            # Update progress
            progress["translated_chapters"][i]["translated"] = True
            progress["last_processed_chapter_index"] = i
            save_translation_progress(progress_file, progress)

    # Now translate additional HTML files
    start_html_index = progress["last_processed_html_index"] + 1

    for i, html_file_info in enumerate(
        progress["translated_html_files"][start_html_index:], start=start_html_index
    ):
        if html_file_info.get("translated", False):
            logger.info(
                f"Skipping already translated HTML file {i + 1}/{len(progress['translated_html_files'])}: {html_file_info['src']}"
            )
            continue

        html_path = Path(epub_translated_dir) / html_file_info["src"]

        if html_path.exists():
            logger.info(
                f"Translating additional HTML file {i + 1}/{len(progress['translated_html_files'])}: {html_file_info['src']}"
            )

            # Read the HTML content
            with open(html_path, "r", encoding="utf-8") as f:
                html_content = f.read()

            # Translate the HTML content
            translated_html = translate_html_content(
                html_content,
                html_file_info["title"],
                translated_book_title,
                source_language,
                target_language,
                client,
                config,
                previous_content,
            )

            # Save the translated HTML
            with open(html_path, "w", encoding="utf-8") as f:
                f.write(translated_html)

            # Store for context in next translation based on config limit
            previous_content_limit = config.get("previous_content_limit", 0)
            if previous_content_limit > 0:
                previous_content = translated_html[:previous_content_limit]
            else:
                previous_content = None

            # Update progress
            progress["translated_html_files"][i]["translated"] = True
            progress["last_processed_html_index"] = i
            save_translation_progress(progress_file, progress)

    # Create the final EPUB file
    output_epub_path = output_dir / f"{translated_book_title}.epub"

    # Create a temporary zip file
    temp_zip = output_dir / "temp.zip"

    # Create the EPUB zip file
    with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
        # First add the mimetype file (must be uncompressed)
        mimetype_path = Path(epub_translated_dir) / "mimetype"
        if mimetype_path.exists():
            zipf.write(mimetype_path, "mimetype", compress_type=zipfile.ZIP_STORED)

        # Add all other files
        for folder_name, subfolders, filenames in os.walk(epub_translated_dir):
            folder_path = Path(folder_name)
            relative_path = folder_path.relative_to(epub_translated_dir)

            # Skip mimetype as it's already added
            if str(relative_path) == "." and "mimetype" in filenames:
                filenames.remove("mimetype")

            # Add all files in the current folder
            for filename in filenames:
                file_path = folder_path / filename
                arcname = Path(relative_path) / filename
                zipf.write(file_path, arcname)

    # Rename the zip file to epub
    shutil.move(temp_zip, output_epub_path)
    logger.success(f"Created translated EPUB at {output_epub_path}")
    return output_epub_path


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(
        description="Translate EPUB files between languages"
    )
    parser.add_argument("--input", "-i", required=True, help="Path to input EPUB file")
    parser.add_argument(
        "--source-lang",
        "-s",
        help="Source language (overrides config.yaml)",
    )
    parser.add_argument(
        "--target-lang",
        "-t",
        help="Target language (overrides config.yaml)",
    )
    parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--resume",
        "-r",
        action="store_true",
        help="Resume previous translation if available",
    )

    args = parser.parse_args()

    # Load configuration
    with open(args.config, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)

    # Get source and target languages from config or defaults
    source_lang = args.source_lang
    if source_lang is None:
        source_lang = config.get("source_language", "English")
        
    target_lang = args.target_lang
    if target_lang is None:
        target_lang = config.get("target_language", "Chinese")

    # Add command line arguments to config
    config["input_epub_path"] = args.input

    # Translate the EPUB
    translate_epub(args.input, source_lang, target_lang, config)


if __name__ == "__main__":
    main()
