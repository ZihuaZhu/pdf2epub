import json
import os
import uuid
import shutil
import fitz  # PyMuPDF
import yaml
import zipfile
import re
import argparse
from io import BytesIO
from datetime import datetime
from pathlib import Path
from PIL import Image
from google import genai
from google.genai.types import (
    GenerateContentConfig,
    HarmBlockThreshold,
    HarmCategory,
    Part,
    SafetySetting,
)


def load_config():
    """Load configuration from config.yaml file."""
    with open("config.yaml", "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    return config


def setup_genai_api(api_key):
    """Setup Google Generative AI API with the provided key."""
    return genai.Client(api_key=api_key)


def load_book_structure(book_title):
    """Load the book structure JSON file."""
    structure_path = Path("output") / Path(book_title) / "book_structure.json"
    with open(structure_path, "r", encoding="utf-8") as file:
        structure = json.load(file)
    return structure


def ensure_directory(directory_path):
    """Ensure a directory exists, create it if it doesn't."""
    Path(directory_path).mkdir(parents=True, exist_ok=True)


def get_pdf_page_count(pdf_path):
    """Get the total number of pages in a PDF file."""
    with fitz.open(pdf_path) as pdf:
        return len(pdf)


def clean_html_response(html_content):
    """Clean the HTML response from Gemini to remove code blocks and other content."""
    if html_content is None:
        return None
        
    # Remove markdown code block markers if present
    html_content = re.sub(r"```html\s*", "", html_content)
    html_content = re.sub(r"```\s*$", "", html_content)

    # Remove any other markdown code block markers
    html_content = re.sub(r"```[a-zA-Z]*\s*", "", html_content)

    # Remove any non-HTML content before or after the actual HTML
    html_match = re.search(
        r"(?:<\!DOCTYPE.*?>|<html.*?>).*?<\/html>", html_content, re.DOTALL
    )
    if html_match:
        html_content = html_match.group(0)

    return html_content.strip()


def save_generation_progress(progress_file, progress_data):
    """Save generation progress to a JSON file."""
    with open(progress_file, "w", encoding="utf-8") as f:
        json.dump(progress_data, f, indent=2)


def load_generation_progress(progress_file, structure=None):
    """Load generation progress from a JSON file."""
    if Path(progress_file).exists():
        with open(progress_file, "r", encoding="utf-8") as f:
            return json.load(f)
    
    # Initialize with default values
    progress = {
        "cover_extracted": False,
        "cover_html_created": False,
        "stylesheet_created": False,
        "toc_ncx_created": False,
        "toc_html_created": False,
        "container_xml_created": False,
        "mimetype_created": False,
        "content_opf_created": False,
        "processed_chapters": [],
        "last_processed_chapter_index": -1,
        "cover_image_filename": "",
        "chapter_titles": [],
    }
    
    # If structure is provided, initialize chapters with generated: false
    if structure:
        progress["chapters"] = []
        for i, chapter in enumerate(structure["chapters"], 1):
            progress["chapters"].append({
                "index": i,
                "title": chapter["title"],
                "generated": False
            })
    
    return progress


def clean_unused_images(epub_dir):
    """Remove images that aren't referenced in any HTML file."""
    print("Cleaning unused images...")
    # Get all image files
    all_images = set()
    for img_path in Path(epub_dir).glob("images/*.*"):
        all_images.add(img_path.name)
    
    # Find referenced images in all HTML files
    referenced_images = set()
    for html_file in Path(epub_dir).glob("**/*.html"):
        with open(html_file, "r", encoding="utf-8") as f:
            content = f.read()
            # Find all image references
            img_refs = re.findall(r'<img src="\.\.\/images\/([^"]+)"', content)
            referenced_images.update(img_refs)
    
    # Add cover image to referenced images (it's always used)
    cover_path = Path(epub_dir) / "content.opf"
    if cover_path.exists():
        with open(cover_path, "r", encoding="utf-8") as f:
            content = f.read()
            cover_match = re.search(r'<item href="([^"]+)" id="cover"', content)
            if cover_match and not cover_match.group(1).startswith("images/"):
                # If it's a direct reference to the cover image
                referenced_images.add(cover_match.group(1))
    
    # Also check titlepage.xhtml for cover image references
    cover_page_path = Path(epub_dir) / "titlepage.xhtml"
    if cover_page_path.exists():
        with open(cover_page_path, "r", encoding="utf-8") as f:
            content = f.read()
            cover_matches = re.findall(r'xlink:href="([^"]+)"', content)
            referenced_images.update(cover_matches)
    
    # Find unused images
    unused_images = all_images - referenced_images
    
    # Remove unused images
    for img_name in unused_images:
        img_path = Path(epub_dir) / "images" / img_name
        try:
            os.remove(img_path)
            print(f"Removed unused image: {img_name}")
        except Exception as e:
            print(f"Error removing {img_name}: {e}")
    
    print(f"Removed {len(unused_images)} unused images.")


def create_toc_ncx(structure, book_title, book_uuid, output_path):
    """Create the toc.ncx file for EPUB navigation."""
    # Start building the NCX content
    ncx_content = f"""<?xml version='1.0' encoding='utf-8'?>
<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="jpn">
  <head>
    <meta content="{book_uuid}" name="dtb:uid"/>
    <meta content="2" name="dtb:depth"/>
    <meta content="calibre (4.23.0)" name="dtb:generator"/>
    <meta content="0" name="dtb:totalPageCount"/>
    <meta content="0" name="dtb:maxPageNumber"/>
  </head>
  <docTitle>
    <text>{book_title}</text>
  </docTitle>
  <navMap>
    <navPoint class="chapter" id="cover" playOrder="1">
      <navLabel>
        <text>表紙</text>
      </navLabel>
      <content src="titlepage.xhtml"/>
    </navPoint>
    <navPoint class="chapter" id="toc" playOrder="2">
      <navLabel>
        <text>目次</text>
      </navLabel>
      <content src="text/toc.html"/>
    </navPoint>"""

    # Add chapters to the NCX
    play_order = 3
    for i, chapter in enumerate(structure["chapters"], 1):
        chapter_id = f"chapter_{i}"
        chapter_filename = f"chapter_{i}.html"

        ncx_content += f"""
    <navPoint class="chapter" id="{chapter_id}" playOrder="{play_order}">
      <navLabel>
        <text>{chapter['title']}</text>
      </navLabel>
      <content src="text/{chapter_filename}"/>
    </navPoint>"""
        play_order += 1

    # Close the NCX file
    ncx_content += """
  </navMap>
</ncx>"""

    # Write the NCX file
    with open(output_path, "w", encoding="utf-8") as ncx_file:
        ncx_file.write(ncx_content)

    print(f"Created toc.ncx at {output_path}")


def extract_cover_image(pdf_path, output_dir):
    """Extract the cover image from the PDF."""
    doc = fitz.open(pdf_path)
    cover_page = doc[0]  # First page is the cover

    # Get list of image objects in the cover page
    image_list = cover_page.get_images(full=True)

    # If no images found, save the whole page as an image
    if not image_list:
        pix = cover_page.get_pixmap(matrix=fitz.Matrix(2, 2))  # Higher resolution
        image_path = os.path.join(output_dir, "cover.jpeg")
        pix.save(image_path)
        return "cover.jpeg"

    # Otherwise, extract the largest image as the cover
    largest_image = None
    max_size = 0

    for img_index, img in enumerate(image_list):
        xref = img[0]
        base_image = doc.extract_image(xref)
        image_bytes = base_image["image"]
        image_ext = base_image["ext"]
        width = base_image["width"]
        height = base_image["height"]

        image_size = width * height
        if image_size > max_size:
            max_size = image_size
            largest_image = (img_index, image_bytes, image_ext)

    if largest_image:
        img_index, image_bytes, image_ext = largest_image
        # Always save as JPEG for better compatibility
        if image_ext.lower() != "jpeg" and image_ext.lower() != "jpg":
            # Convert to JPEG if it's not already
            img = Image.open(BytesIO(image_bytes))
            img_buffer = BytesIO()
            img.convert("RGB").save(img_buffer, format="JPEG", quality=95)
            image_bytes = img_buffer.getvalue()
            image_ext = "jpeg"

        image_filename = f"cover.{image_ext}"
        image_path = os.path.join(output_dir, image_filename)

        with open(image_path, "wb") as img_file:
            img_file.write(image_bytes)

        return image_filename

    return None


def extract_images_from_pdf_page(pdf_doc, page_num, images_dir, chapter_index, base_counter=1):
    """Extract meaningful images from a specific PDF page with improved filtering."""
    page = pdf_doc[page_num]
    image_list = page.get_images(full=True)
    extracted_images = []
    
    counter = base_counter
    for img_index, img in enumerate(image_list):
        xref = img[0]
        base_image = pdf_doc.extract_image(xref)
        
        # More aggressively filter small images and better detect full-page text
        width = base_image["width"]
        height = base_image["height"]
        
        # Skip very small images (likely icons or decorations)
        if width < 100 or height < 100:
            continue
        
        # Skip images that are likely full-page text
        # This heuristic looks at image dimensions compared to page dimensions
        page_width, page_height = page.rect.width, page.rect.height
        if (width > 0.9 * page_width and height > 0.9 * page_height):
            # This might be a full page scan - skip unless it's actually an image
            continue
            
        image_bytes = base_image["image"]
        image_ext = base_image["ext"]
        
        # Convert to JPEG for compatibility
        if image_ext.lower() not in ["jpeg", "jpg"]:
            try:
                img_obj = Image.open(BytesIO(image_bytes))
                img_buffer = BytesIO()
                img_obj.convert("RGB").save(img_buffer, format="JPEG", quality=95)
                image_bytes = img_buffer.getvalue()
                image_ext = "jpg"
            except Exception as e:
                print(f"Error converting image: {e}")
                continue
        
        image_filename = f"chapter_{chapter_index}_img_{counter}.{image_ext}"
        image_path = os.path.join(images_dir, image_filename)
        
        with open(image_path, "wb") as img_file:
            img_file.write(image_bytes)
        
        extracted_images.append({
            "filename": image_filename,
            "path": image_path,
            "page": page_num,
            "width": width,
            "height": height
        })
        
        counter += 1
    
    return extracted_images, counter


def create_cover_html(cover_image_filename, book_title, output_path):
    """Create XHTML file for the cover."""
    # Get image dimensions
    try:
        with Image.open(Path(output_path).parent / cover_image_filename) as img:
            width, height = img.size
    except Exception:
        width, height = 600, 800  # Default dimensions if unable to get actual size

    cover_html = f"""<?xml version='1.0' encoding='utf-8'?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="ja">
    <head>
        <meta http-equiv="Content-Type" content="text/html; charset=UTF-8"/>
        <meta name="calibre:cover" content="true"/>
        <title>Cover</title>
        <style type="text/css" title="override_css">
            @page {{padding: 0pt; margin:0pt}}
            body {{ text-align: center; padding:0pt; margin: 0pt; }}
        </style>
    </head>
    <body>
        <div>
            <svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" version="1.1" width="100%" height="100%" viewBox="0 0 {width} {height}" preserveAspectRatio="none">
                <image width="{width}" height="{height}" xlink:href="{cover_image_filename}"/>
            </svg>
        </div>
    </body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as html_file:
        html_file.write(cover_html)

    print(f"Created cover XHTML at {output_path}")


def create_toc_html(structure, book_title, output_path, client, pdf_path, config):
    """Create HTML file for the table of contents using Gemini."""
    # Get TOC page range from the structure
    toc_start = structure["table_of_contents"]["start_page"]
    toc_end = structure["table_of_contents"]["end_page"]
    
    # Extract only the TOC pages from the PDF
    with fitz.open(pdf_path) as full_pdf:
        # Create a new PDF with just the TOC pages
        toc_pdf = fitz.open()
        for page_num in range(toc_start - 1, toc_end):  # Convert to 0-based indexing
            if page_num < len(full_pdf):  # Ensure we don't go out of bounds
                toc_pdf.insert_pdf(full_pdf, from_page=page_num, to_page=page_num)
        
        # Save to a temporary file
        temp_pdf_path = Path(output_path).parent / "temp_toc.pdf"
        toc_pdf.save(temp_pdf_path)
        toc_pdf.close()
    
    # Load only the TOC PDF
    with open(temp_pdf_path, "rb") as f:
        pdf_data = f.read()
    
    prompt = f"""
    Create an HTML file for the table of contents of this book "{book_title}".
    The PDF provided contains only the table of contents pages.
    
    The HTML should include:
    1. A clear title "目次" (Table of Contents)
    2. A well-formatted list of all chapters and subchapters with their original titles in Japanese
    3. Each entry should link to the appropriate chapter in the format:
       - All chapters must link to "chapter_X.html" (where X is the chapter number)
       - Do NOT use any other naming formats (like introduction.html, i.e., introduction should be chapter 1)

    Please format the HTML to be clean, well-structured, and with appropriate CSS styling.
    
    Return only the complete HTML code without any other commentary.
    """
    
    # Create parts for the multimodal input - text and PDF data
    parts = [
        prompt,
        Part.from_bytes(data=pdf_data, mime_type="application/pdf"),
    ]
    
    # Get model from config with fallback
    model = config.get("model", "gemini-2.5-pro-preview-03-25")
    
    # Generate content
    response = client.models.generate_content(
        model=model,
        contents=parts,
        config=GenerateContentConfig(
            temperature=0.1,
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
        ),
    )
    
    # Get number of retries from config
    num_retries = config.get("num_retries", 3)
    retry_count = 0
    html_content = None
    
    while html_content is None and retry_count < num_retries:
        if retry_count > 0:
            print(f"Retry attempt {retry_count} for TOC HTML generation...")
            
        # Generate content
        response = client.models.generate_content(
            model=model,
            contents=parts,
            config=GenerateContentConfig(
                temperature=0.1,
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
            ),
        )
        
        # Clean the HTML response
        html_content = clean_html_response(response.text)
        retry_count += 1
    
    if html_content is None:
        raise ValueError(f"Failed to generate TOC HTML after {num_retries} attempts")
    
    # Write the HTML to the output file
    with open(output_path, "w", encoding="utf-8") as html_file:
        html_file.write(html_content)
    
    # Clean up temporary PDF
    try:
        os.remove(temp_pdf_path)
    except Exception as e:
        print(f"Warning: Could not remove temporary TOC PDF: {e}")
    
    print(f"Created TOC HTML at {output_path}")


def create_chapter_html(
    chapter,
    structure,
    chapter_index,
    book_title,
    output_path,
    client,
    pdf_path,
    images_dir,
    previous_chapters,
    config,
):
    """Create HTML file for a chapter using Gemini with simplified image handling."""
    # Get chapter page range with buffer (3 pages before and after)
    start_page = max(1, chapter["start_page"] - 3)  # Don't go below page 1
    end_page = min(chapter["end_page"] + 3, get_pdf_page_count(pdf_path))  # Don't exceed PDF length
    
    # Original chapter bounds for the prompt
    actual_start = chapter["start_page"]
    actual_end = chapter["end_page"]
    chapter_title = chapter["title"]

    # Extract only the pages we need from the PDF
    with fitz.open(pdf_path) as full_pdf:
        # Create a new PDF with just the chapter pages plus buffer
        chapter_pdf = fitz.open()
        for page_num in range(start_page - 1, end_page):  # Convert to 0-based indexing
            if page_num < len(full_pdf):  # Ensure we don't go out of bounds
                chapter_pdf.insert_pdf(full_pdf, from_page=page_num, to_page=page_num)
        
        # Save to a temporary file
        temp_pdf_path = Path(output_path).parent / f"temp_chapter_{chapter_index}.pdf"
        chapter_pdf.save(temp_pdf_path)
        chapter_pdf.close()
    
    # Load only the chapter PDF
    with open(temp_pdf_path, "rb") as f:
        pdf_data = f.read()

    prompt = f"""
    Convert the chapter "{chapter_title}" from the book "{book_title}" into clean HTML format.
    
    The PDF contains pages {start_page} to {end_page}, but you should focus on translating ONLY 
    the actual chapter content (pages {actual_start} to {actual_end} in the original PDF).
    
    Create a clean, well-formatted HTML with proper heading structure and preserve all original Japanese text.
    """"""
    IMPORTANT FOR IMAGES:
    - When you encounter an important image, diagram, or illustration, insert an image placeholder like this:
      <div class="image-placeholder" id="img1" data-page="{actual_relative_page}" data-description="Brief description of the image"></div>
    - The data-page attribute should be the page number RELATIVE to the start of the chapter (0 = first page, 1 = second page, etc.)
    - Only include placeholders for meaningful images (photos, diagrams, illustrations) - NOT for decorative elements or text-only pages
    - Include a brief description of what the image shows in the data-description attribute
    Other requirements:
    - Keep all original text formatting and structure
    - Preserve all footnotes and move them to the end of the chapter with proper links
    - Use the following CSS stylesheet (already defined at ../stylesheet.css):
    ```
    @namespace h "http://www.w3.org/1999/xhtml";
    body {
        font-family: "Hiragino Mincho ProN", "MS Mincho", serif;
        line-height: 1.8;
        max-width: 800px;
        margin: 2em auto;
        padding: 0 1em;
        background-color: #fdfdfd;
        color: #333;
    }
    h1 {
        text-align: center;
        margin-top: 1em;
        margin-bottom: 2em;
        font-weight: bold;
        font-size: 2em;
        border-bottom: 2px solid #ccc;
        padding-bottom: 0.5em;
    }
    h2 {
        font-size: 1.5em;
        font-weight: bold;
        margin-top: 2.5em;
        margin-bottom: 1em;
        border-bottom: 1px solid #ddd;
        padding-bottom: 0.3em;
    }
    h3 {
        font-size: 1.2em;
        font-weight: bold;
        margin-top: 2em;
        margin-bottom: 0.8em;
    }
    p {
        margin-bottom: 1.2em;
        text-indent: 1em; /* Add indentation for paragraphs */
        text-align: justify;
    }
    .image-placeholder {
        width: 100%;
        height: 200px; /* Adjust height as needed */
        background-color: #eee;
        border: 1px dashed #ccc;
        display: flex;
        align-items: center;
        justify-content: center;
        margin: 1.5em 0;
        font-style: italic;
        color: #888;
    }
    .image-placeholder::before {
        content: "Image Placeholder (ID: " attr(id) ")";
    }
    .footnotes {
        margin-top: 4em;
        padding-top: 1em;
        border-top: 1px solid #ccc;
        font-size: 0.9em;
    }
    .footnotes h2 {
        font-size: 1.2em;
        border-bottom: none;
        margin-bottom: 1em;
    }
    .footnotes ol {
        padding-left: 1.5em;
        list-style-type: decimal;
    }
    .footnote-item {
        margin-bottom: 0.8em;
        line-height: 1.6;
    }
    .footnote-item p {
            text-indent: 0; /* No indent for footnote paragraphs if needed */
            margin-bottom: 0.5em;
    }
    sup {
        font-size: 0.8em;
        vertical-align: super;
    }
    sup a {
        text-decoration: none;
        color: #0066cc;
    }
    sup a:hover {
        text-decoration: underline;
    }
    .footnote-item a[href^="#fnref"] {
        text-decoration: none;
        color: #0066cc;
        margin-left: 0.3em;
    }
    .footnote-item a[href^="#fnref"]:hover {
        text-decoration: underline;
    }
    /* Specific formatting from text */
    .inline-note { /* For things like ビルドゥングスロマン */
        font-size: 0.85em;
    }
    ```
    
    Return ONLY the HTML content without any other commentary.
    """

    # Create parts for the multimodal input - text and PDF data
    parts = [
        prompt,
        Part.from_bytes(data=pdf_data, mime_type="application/pdf"),
    ]

    # Get model from config with fallback
    model = config.get("model", "gemini-2.5-pro-preview-03-25")
    
    # Generate content
    response = client.models.generate_content(
        model=model,
        contents=parts,
        config=GenerateContentConfig(
            temperature=0.1,
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
        ),
    )

    # Get number of retries from config
    num_retries = config.get("num_retries", 3)
    retry_count = 0
    html_content = None
    
    while html_content is None and retry_count < num_retries:
        if retry_count > 0:
            print(f"Retry attempt {retry_count} for chapter {chapter_index} HTML generation...")
            
        # Generate content
        response = client.models.generate_content(
            model=model,
            contents=parts,
            config=GenerateContentConfig(
                temperature=0.1,
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
            ),
        )
        
        # Clean the HTML response
        html_content = clean_html_response(response.text)
        retry_count += 1
    
    if html_content is None:
        raise ValueError(f"Failed to generate chapter {chapter_index} HTML after {num_retries} attempts")
    
    # Process image placeholders
    with fitz.open(temp_pdf_path) as chapter_pdf:
        image_counter = 1
        
        # Find all image placeholders in the HTML
        placeholder_matches = re.finditer(r'<div class="image-placeholder" id="([^"]+)" data-page="(\d+)" data-description="([^"]+)"></div>', html_content)
        
        for match in placeholder_matches:
            # img_id = match.group(1)
            relative_page = int(match.group(2))
            description = match.group(3)
            
            # Calculate the actual page in the PDF
            actual_page = min(relative_page, len(chapter_pdf) - 1)
            
            # Extract images from this page
            extracted_images, _ = extract_images_from_pdf_page(
                chapter_pdf, actual_page, images_dir, chapter_index, image_counter
            )
            
            # If images were found, replace the placeholder with an actual image tag
            if extracted_images:
                img = extracted_images[0]  # Use the first extracted image
                img_tag = f'<img src="../images/{img["filename"]}" alt="{description}" class="chapter-image" />'
                placeholder = match.group(0)
                html_content = html_content.replace(placeholder, img_tag)
                image_counter += 1

    # Write the HTML to the output file
    with open(output_path, "w", encoding="utf-8") as html_file:
        html_file.write(html_content)
    
    # Clean up temporary PDF
    try:
        os.remove(temp_pdf_path)
    except Exception as e:
        print(f"Warning: Could not remove temporary PDF: {e}")

    print(f"Created Chapter {chapter_index} HTML at {output_path}")
    return chapter_title


def create_container_xml(output_path):
    """Create the META-INF/container.xml file."""
    container_xml = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
   <rootfiles>
      <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
   </rootfiles>
</container>"""

    with open(output_path, "w", encoding="utf-8") as xml_file:
        xml_file.write(container_xml)


def create_mimetype(output_path):
    """Create the mimetype file."""
    with open(output_path, "w", encoding="utf-8") as mimetype_file:
        mimetype_file.write("application/epub+zip")


def create_content_opf(
    book_title, book_uuid, author, chapters, cover_filename, output_path, epub_dir
):
    """Create the content.opf file."""
    # Get current timestamp in ISO format
    timestamp = datetime.now().isoformat(timespec="seconds")

    # Start building the OPF content
    opf_content = f"""<?xml version='1.0' encoding='utf-8'?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="uuid_id" version="2.0">
  <metadata xmlns:calibre="http://calibre.kovidgoyal.net/2009/metadata" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:opf="http://www.idpf.org/2007/opf" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
    <dc:date>{timestamp}</dc:date>
    <dc:title>{book_title}</dc:title>
    <dc:identifier id="uuid_id" opf:scheme="uuid">{book_uuid}</dc:identifier>
    <dc:identifier opf:scheme="calibre">{book_uuid}</dc:identifier>
    <dc:language>ja</dc:language>
    <dc:contributor opf:role="bkp">calibre (4.23.0) [https://calibre-ebook.com]</dc:contributor>
    <meta name="calibre:title_sort" content="{book_title}"/>
    <meta name="cover" content="cover"/>
    <dc:creator opf:file-as="{author}" opf:role="aut">{author}</dc:creator>
    <meta name="calibre:timestamp" content="{timestamp}+00:00"/>
  </metadata>
  <manifest>
    <item href="{cover_filename}" id="cover" media-type="image/jpeg"/>
    <item href="titlepage.xhtml" id="titlepage" media-type="application/xhtml+xml"/>
    <item href="text/toc.html" id="toc" media-type="application/xhtml+xml"/>
"""

    # Add chapters to manifest
    for i in range(1, len(chapters) + 1):
        opf_content += f'    <item href="text/chapter_{i}.html" id="chapter_{i}" media-type="application/xhtml+xml"/>\n'

    # Add images to manifest
    image_id = 1
    for image_file in Path(epub_dir).glob("images/*.jpg"):
        opf_content += f'    <item href="images/{image_file.name}" id="img_{image_id}" media-type="image/jpeg"/>\n'
        image_id += 1

    for image_file in Path(epub_dir).glob("images/*.jpeg"):
        opf_content += f'    <item href="images/{image_file.name}" id="img_{image_id}" media-type="image/jpeg"/>\n'
        image_id += 1

    for image_file in Path(epub_dir).glob("images/*.png"):
        opf_content += f'    <item href="images/{image_file.name}" id="img_{image_id}" media-type="image/png"/>\n'
        image_id += 1

    # Add remaining required items
    opf_content += """    <item href="stylesheet.css" id="css" media-type="text/css"/>
    <item href="toc.ncx" id="ncx" media-type="application/x-dtbncx+xml"/>
  </manifest>
  <spine toc="ncx" page-progression-direction="rtl">
    <itemref idref="titlepage"/>
    <itemref idref="toc"/>
"""

    # Add chapters to spine
    for i in range(1, len(chapters) + 1):
        opf_content += f'    <itemref idref="chapter_{i}"/>\n'

    # Close spine and add guide
    opf_content += """  </spine>
  <guide>
    <reference href="text/toc.html" title="目次" type="toc"/>
    <reference href="titlepage.xhtml" title="Cover" type="cover"/>
    <reference href="text/chapter_1.html" title="Start" type="text"/>
  </guide>
</package>"""

    # Write the OPF file
    with open(output_path, "w", encoding="utf-8") as opf_file:
        opf_file.write(opf_content)


def create_stylesheet(output_path):
    """Create a basic CSS stylesheet."""
    css_content = """@namespace h "http://www.w3.org/1999/xhtml";
body {
    font-family: "Hiragino Mincho ProN", "MS Mincho", serif;
    line-height: 1.8;
    max-width: 800px;
    margin: 2em auto;
    padding: 0 1em;
    background-color: #fdfdfd;
    color: #333;
}
h1 {
    text-align: center;
    margin-top: 1em;
    margin-bottom: 2em;
    font-weight: bold;
    font-size: 2em;
    border-bottom: 2px solid #ccc;
    padding-bottom: 0.5em;
}
h2 {
    font-size: 1.5em;
    font-weight: bold;
    margin-top: 2.5em;
    margin-bottom: 1em;
    border-bottom: 1px solid #ddd;
    padding-bottom: 0.3em;
}
h3 {
    font-size: 1.2em;
    font-weight: bold;
    margin-top: 2em;
    margin-bottom: 0.8em;
}
p {
    margin-bottom: 1.2em;
    text-indent: 1em; /* Add indentation for paragraphs */
    text-align: justify;
}
.image-placeholder {
    width: 100%;
    height: 200px; /* Adjust height as needed */
    background-color: #eee;
    border: 1px dashed #ccc;
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 1.5em 0;
    font-style: italic;
    color: #888;
}
.image-placeholder::before {
    content: "Image Placeholder (ID: " attr(id) ")";
}
.footnotes {
    margin-top: 4em;
    padding-top: 1em;
    border-top: 1px solid #ccc;
    font-size: 0.9em;
}
.footnotes h2 {
    font-size: 1.2em;
    border-bottom: none;
    margin-bottom: 1em;
}
.footnotes ol {
    padding-left: 1.5em;
    list-style-type: decimal;
}
.footnote-item {
    margin-bottom: 0.8em;
    line-height: 1.6;
}
.footnote-item p {
        text-indent: 0; /* No indent for footnote paragraphs if needed */
        margin-bottom: 0.5em;
}
sup {
    font-size: 0.8em;
    vertical-align: super;
}
sup a {
    text-decoration: none;
    color: #0066cc;
}
sup a:hover {
    text-decoration: underline;
}
.footnote-item a[href^="#fnref"] {
    text-decoration: none;
    color: #0066cc;
    margin-left: 0.3em;
}
.footnote-item a[href^="#fnref"]:hover {
    text-decoration: underline;
}
/* Specific formatting from text */
.inline-note { /* For things like ビルドゥングスロマン */
    font-size: 0.85em;
}"""

    with open(output_path, "w", encoding="utf-8") as css_file:
        css_file.write(css_content)


def create_epub(book_title, epub_dir):
    """Create an EPUB file by zipping the contents."""
    output_epub = Path("output") / Path(book_title) / f"{book_title}.epub"

    # Create a temporary zip file
    temp_zip = Path("output") / Path(book_title) / "temp.zip"

    # Create the EPUB zip file
    with zipfile.ZipFile(temp_zip, "w", zipfile.ZIP_DEFLATED) as zipf:
        # First add the mimetype file (must be uncompressed)
        zipf.write(epub_dir / "mimetype", "mimetype", compress_type=zipfile.ZIP_STORED)

        # Add all other files
        for folder_name, subfolders, filenames in os.walk(epub_dir):
            folder_path = Path(folder_name)
            relative_path = folder_path.relative_to(epub_dir)

            # Skip mimetype as it's already added
            if str(relative_path) == "." and "mimetype" in filenames:
                filenames.remove("mimetype")

            # Add all files in the current folder
            for filename in filenames:
                file_path = folder_path / filename
                arcname = Path(relative_path) / filename
                zipf.write(file_path, arcname)

    # Rename the zip file to epub
    shutil.move(temp_zip, output_epub)
    print(f"Created EPUB at {output_epub}")
    return output_epub


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser(description="Generate EPUB from PDF")
    parser.add_argument("--input", "-i", required=True, help="Path to input PDF file")
    parser.add_argument("--resume", "-r", action="store_true", help="Resume previous generation if available")
    parser.add_argument(
        "--config",
        "-c",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    args = parser.parse_args()

    # Load configuration
    with open(args.config, "r", encoding="utf-8") as file:
        config = yaml.safe_load(file)
    api_key = config.get("google_api_key")
    book_title = config.get("title")
    author = config.get("author")

    # Check if API key exists
    if not api_key:
        raise ValueError("Google API key not found in config.yaml")
        
    # Check if title exists
    if not book_title:
        # Fallback to PDF filename if title not in config
        book_title = Path(args.input).stem
        print(f"Warning: No title found in config, using PDF filename: {book_title}")

    # Setup Gemini API
    client = setup_genai_api(api_key)

    # Load book structure
    structure = load_book_structure(book_title)

    # Define output directories
    epub_dir = Path("output") / Path(book_title) / "epub"
    images_dir = epub_dir / "images"
    text_dir = epub_dir / "text"
    meta_inf_dir = epub_dir / "META-INF"
    progress_file = Path("output") / Path(book_title) / "generation_progress.json"

    # Check if we're resuming a previous generation
    resuming = epub_dir.exists() and progress_file.exists()
    progress = None

    if resuming:
        print("Found existing generation. Attempting to resume...")
        progress = load_generation_progress(progress_file)
        # Generate a UUID for the book (use the same one if resuming)
        book_uuid = progress.get("book_uuid", str(uuid.uuid4()))
    else:
        # Start fresh
        print("Starting new EPUB generation...")
        # Generate a UUID for the book
        book_uuid = str(uuid.uuid4())
        # Initialize progress tracking with structure
        progress = load_generation_progress(progress_file, structure)
        progress["book_uuid"] = book_uuid

    # Ensure directories exist
    ensure_directory(epub_dir)
    ensure_directory(images_dir)
    ensure_directory(text_dir)
    ensure_directory(meta_inf_dir)

    # Path to the PDF file
    pdf_path = Path(args.input)

    # Create the mimetype file if not already done
    if not progress["mimetype_created"]:
        create_mimetype(epub_dir / "mimetype")
        progress["mimetype_created"] = True
        save_generation_progress(progress_file, progress)
    else:
        print("Skipping mimetype creation (already done)")

    # Create the container.xml file if not already done
    if not progress["container_xml_created"]:
        create_container_xml(meta_inf_dir / "container.xml")
        progress["container_xml_created"] = True
        save_generation_progress(progress_file, progress)
    else:
        print("Skipping container.xml creation (already done)")

    # Extract and save the cover image if not already done
    cover_image_filename = progress["cover_image_filename"]
    if not progress["cover_extracted"]:
        cover_image_filename = extract_cover_image(pdf_path, epub_dir)
        progress["cover_image_filename"] = cover_image_filename
        progress["cover_extracted"] = True
        save_generation_progress(progress_file, progress)
    else:
        print(f"Skipping cover extraction (already done): {cover_image_filename}")

    # Create the titlepage XHTML if not already done
    if not progress["cover_html_created"]:
        create_cover_html(cover_image_filename, book_title, epub_dir / "titlepage.xhtml")
        progress["cover_html_created"] = True
        save_generation_progress(progress_file, progress)
    else:
        print("Skipping cover HTML creation (already done)")

    # Create the stylesheet if not already done
    if not progress["stylesheet_created"]:
        create_stylesheet(epub_dir / "stylesheet.css")
        progress["stylesheet_created"] = True
        save_generation_progress(progress_file, progress)
    else:
        print("Skipping stylesheet creation (already done)")

    # Create the toc.ncx file if not already done
    if not progress["toc_ncx_created"]:
        create_toc_ncx(structure, book_title, book_uuid, epub_dir / "toc.ncx")
        progress["toc_ncx_created"] = True
        save_generation_progress(progress_file, progress)
    else:
        print("Skipping toc.ncx creation (already done)")

    # Create HTML for the table of contents if not already done
    toc_html_path = text_dir / "toc.html"
    if not progress["toc_html_created"]:
        create_toc_html(structure, book_title, toc_html_path, client, pdf_path, config)
        progress["toc_html_created"] = True
        save_generation_progress(progress_file, progress)
    else:
        print("Skipping TOC HTML creation (already done)")

    # Process each chapter
    previous_chapters = []
    chapter_titles = progress["chapter_titles"]
    
    # Ensure chapters array exists in progress
    if "chapters" not in progress:
        progress["chapters"] = []
        for i, chapter in enumerate(structure["chapters"], 1):
            progress["chapters"].append({
                "index": i,
                "title": chapter["title"],
                "generated": False
            })
        save_generation_progress(progress_file, progress)

    for i, chapter in enumerate(structure["chapters"], 1):
        # Check if chapter is already processed
        chapter_processed = False
        if i <= progress["last_processed_chapter_index"]:
            # For backward compatibility
            chapter_processed = True
        elif "chapters" in progress:
            # Find the chapter in the chapters array
            for ch in progress["chapters"]:
                if ch["index"] == i and ch["generated"]:
                    chapter_processed = True
                    break
        
        if chapter_processed:
            print(f"Skipping chapter {i} (already processed)")
            # Load the chapter content for context
            chapter_html_path = text_dir / f"chapter_{i}.html"
            if chapter_html_path.exists():
                with open(chapter_html_path, "r", encoding="utf-8") as f:
                    chapter_content = f.read()
                    previous_chapters.append(chapter_content)
            continue

        chapter_html_path = text_dir / f"chapter_{i}.html"
        chapter_title = create_chapter_html(
            chapter,
            structure,
            i,
            book_title,
            chapter_html_path,
            client,
            pdf_path,
            images_dir,
            previous_chapters,
            config,
        )
        
        # Update progress
        if len(chapter_titles) < i:
            chapter_titles.append(chapter_title)
        else:
            chapter_titles[i - 1] = chapter_title
            
        progress["chapter_titles"] = chapter_titles
        progress["last_processed_chapter_index"] = i
        
        # Update the chapter's generated status in the chapters array
        for ch in progress["chapters"]:
            if ch["index"] == i:
                ch["generated"] = True
                break
        
        save_generation_progress(progress_file, progress)

        # Add processed chapter to context for next chapters
        with open(chapter_html_path, "r", encoding="utf-8") as f:
            chapter_content = f.read()
            previous_chapters.append(chapter_content)
            # Save money by only keeping the most recent chapter
            # previous_chapters = [chapter_content]

    # Create the content.opf file if not already done
    if not progress["content_opf_created"]:
        create_content_opf(
            book_title,
            book_uuid,
            author,
            chapter_titles,
            cover_image_filename,
            epub_dir / "content.opf",
            epub_dir,
        )
        progress["content_opf_created"] = True
        save_generation_progress(progress_file, progress)
    else:
        print("Skipping content.opf creation (already done)")
    
    # Clean up unused images before finalizing the EPUB
    clean_unused_images(epub_dir)
    
    # Create the final EPUB file
    epub_path = create_epub(book_title, epub_dir)

    print(f"EPUB creation complete! File saved to: {epub_path}")


if __name__ == "__main__":
    main()
