# PDF2EPUB User Guide | [中文](README.md)

PDF2EPUB is a powerful tool that converts PDF books to EPUB format and can translate EPUB books from one language to another. The tool leverages Google's Gemini API for PDF parsing and translation, and uses S3-compatible storage for file saving and synchronization.

## [Recommended] Using GitHub Actions

**Since Google Gemini API may not be accessible in some regions (like mainland China), it's strongly recommended to use GitHub Actions for automated processing.**

1. Fork this repository to your GitHub account

2. Go to the `Settings` tab of your forked repository

3. Go to the `Secrets and variables - Actions` tab

4. Add a new secret named `CONFIG` with the content of your complete `config.yaml` file (see configuration details below)

5. On the GitHub repository page, go to the Actions tab

6. Choose the workflow to run:
   - `Convert pdf to epub` - Convert PDF to EPUB
   - `Translate EPUB` - Translate an EPUB file

7. Click the "Run workflow" button to start processing

8. Once processing is complete, the results will be saved in your configured S3 storage

### Setting Up S3-Compatible Storage

#### Option 1: Backblaze B2 (Recommended)

If you don't have a US credit card, you can get 10GB of free storage from [Backblaze](https://www.backblaze.com/):

1. Register for a Backblaze account
2. Enable B2 Cloud Storage in [My Settings](https://secure.backblaze.com/account_settings.htm)
3. Generate a new master application key on the [Application Keys](https://secure.backblaze.com/app_keys.htm) page (you can record the keyID and applicationKey, but they won't be used)
4. Create an S3 bucket, for example named `translator`
5. Record the Endpoint, add `https://` to the beginning to get your `s3_endpoint`
6. Add a new application key, select access to all buckets
7. Record the keyID (corresponds to `s3_access_key_id` in the config) and applicationKey (corresponds to `s3_secret_access_key` in the config)

#### Option 2: Cloudflare R2

If you have a US credit card, you can get 10GB of free storage from [Cloudflare](https://developers.cloudflare.com/r2/):

1. Log in to your Cloudflare account
2. Go to R2 - Manage R2 API Tokens - Create API Token
3. Allow read and write permissions
4. Record the access key, secret key, and endpoint (complete URL, including https://)
5. Create an S3 bucket, for example named `book`

### Getting a Google API Key

1. Visit [Google AI Studio](https://makersuite.google.com/app/apikey)
2. Create an API key
3. Copy the key to the `google_api_key` field in your configuration file

### Configuration File Details

Create a `config.yaml` file with the following information:

```yaml
title: Original book title
target_title: Translated book title
author: Author name
google_api_key: Your Google API key
model: gemini-2.5-pro-preview-03-25
target_language: Chinese
source_language: English
s3_access_key_id: Your S3 access key ID
s3_secret_access_key: Your S3 secret access key
s3_bucket_name: Your S3 bucket name
s3_endpoint: Your S3 endpoint URL
num_retries: 3  # Number of retries when API calls fail
max_backoff_seconds: 30  # Maximum backoff time in seconds between retries
previous_content_limit: 0  # Number of characters to use as context for translation (0 means no context, can reduce token consumption)
```

## Local Usage

If you have direct access to Google Gemini API, you can also run locally:

### System Requirements

- Python 3.11+
- Poetry (dependency management)
- Google Gemini API key
- S3-compatible storage (optional, for file synchronization and backup)

### Installation Steps

1. Clone the repository:

```bash
git clone https://github.com/yourusername/pdf2epub.git
cd pdf2epub
```

2. Install dependencies using Poetry:

```bash
pip install poetry
poetry install
```

3. Copy the example configuration file:

```bash
cp config.yaml.example config.yaml
```

4. Edit the `config.yaml` file with the necessary information

### Usage Instructions

1. Place your PDF file at `output/book_title/input.pdf`

2. Run PDF structure analysis:

```bash
python src/breakdown.py -c config.yaml -i output/book_title/input.pdf
```

3. Generate EPUB:

```bash
python src/generate_epub.py --input output/book_title/input.pdf --config config.yaml
```

4. Translate EPUB (optional):

```bash
python src/translate_epub.py --input output/book_title/input.epub --config config.yaml
```

## Features

- Convert PDF books to EPUB format
- Translate EPUB books (supports multiple languages)
- Automatically extract and process book structure (table of contents, chapters, etc.)
- Preserve original formatting and images
- Support compression for large PDF files
- Use S3-compatible storage for file synchronization and backup
- Support GitHub Actions for automated processing

## File Path Format

Processed files will be organized in the following structure:

```
output/
└── book_title/
    ├── input.pdf             # Original PDF file
    ├── book_structure.json   # Book structure data
    ├── book_title.epub       # Generated EPUB file
    ├── translated_title.epub # Translated EPUB file (if translation was performed)
    ├── generation_progress.json  # EPUB generation progress
    ├── translation_progress.json # Translation progress
    ├── extract/              # Extracted EPUB content
    ├── translated/           # Translated content
    └── epub/                 # EPUB build files
```

## Configuration Details

Depending on your needs, some configuration items may be optional:

- If you're only converting PDF to EPUB (no translation):
  - You don't need to set `target_title`, `target_language`, or `source_language`
  
- If you're only translating an EPUB (not generating from PDF):
  - All configuration items are required

## Notes

- Currently, only the gemini-pro-2.5 model is recommended, as it's the only model that can convert PDF directly to HTML
- Avoid special characters in book titles to prevent file path issues
- PDF file size is limited to 45MB; files exceeding this limit will be automatically compressed
- Processing large PDFs may take a significant amount of time, please be patient
- The translation process may temporarily fail due to API limitations, but the system will automatically retry
- **The EPUB generation feature has primarily been tested with Japanese books**; other languages may require adjustments
- **Recommended: Use Calibre for format conversion**: Since the HTML generated by Gemini may not fully comply with standards, it's recommended to use Calibre for format conversion before input and after output to ensure optimal compatibility

## Contributing

Pull requests and issue creation are welcome to improve this project.
