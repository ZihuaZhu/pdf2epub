import time
import functools
import httpx
from loguru import logger
from google.genai.types import (
    GenerateContentConfig,
    HarmBlockThreshold,
    HarmCategory,
    SafetySetting,
    HttpOptions,
)

# Set Gemini API timeout to 60 minutes (in milliseconds)
GEMINI_TIMEOUT = 60 * 60 * 1000  # 60 minutes


def setup_genai_client(api_key):
    """
    Setup Google Generative AI API client with the provided key and extended timeout.
    
    Args:
        api_key (str): The API key for Gemini
        
    Returns:
        genai.Client: The configured Gemini client
    """
    from google import genai
    return genai.Client(
        api_key=api_key,
        http_options=HttpOptions(timeout=GEMINI_TIMEOUT)
    )


def get_default_generation_config(temperature=0.1):
    """
    Returns a default GenerateContentConfig with safety settings set to BLOCK_NONE.
    
    Args:
        temperature (float): The temperature to use for generation
        
    Returns:
        GenerateContentConfig: The default generation config
    """
    return GenerateContentConfig(
        temperature=temperature,
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


def retry_with_exponential_backoff(max_retries=3, max_backoff=30):
    """
    Decorator that retries the decorated function with exponential backoff
    when network-related exceptions occur.
    
    Args:
        max_retries (int): Maximum number of retry attempts
        max_backoff (int): Maximum backoff time in seconds
        
    Returns:
        The decorated function
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            retry_count = 0
            result = None
            
            while result is None and retry_count < max_retries:
                try:
                    if retry_count > 0:
                        # Calculate exponential backoff with jitter
                        backoff_time = min(2 ** retry_count + (0.1 * retry_count), max_backoff)
                        logger.warning(f"Retry attempt {retry_count} for {func.__name__}. Waiting {backoff_time:.2f}s...")
                        time.sleep(backoff_time)
                    
                    # Call the original function
                    result = func(*args, **kwargs)
                    
                except httpx.RemoteProtocolError as e:
                    logger.error(f"RemoteProtocolError during {func.__name__}: {e}")
                    retry_count += 1
                    continue
                    
                except httpx.ReadTimeout as e:
                    logger.error(f"ReadTimeout during {func.__name__}: {e}")
                    retry_count += 1
                    continue
                    
                except httpx.ConnectTimeout as e:
                    logger.error(f"ConnectTimeout during {func.__name__}: {e}")
                    retry_count += 1
                    continue
                    
                except httpx.HTTPError as e:
                    logger.error(f"HTTPError during {func.__name__}: {e}")
                    retry_count += 1
                    continue
                    
                except Exception as e:
                    logger.error(f"Unexpected error during {func.__name__}: {e}")
                    retry_count += 1
                    continue
                
                # If we got here with no result, increment retry counter
                if result is None:
                    retry_count += 1
            
            if result is None:
                raise ValueError(f"Failed to execute {func.__name__} after {max_retries} attempts")
                
            return result
        
        return wrapper
    
    return decorator


def generate_content_with_retry(client, model, contents, config=None, max_retries=3, max_backoff=30, operation_name="API call", use_streaming=True):
    """
    Helper function to generate content with retry logic for network-related exceptions.
    Supports both streaming and non-streaming modes.
    
    Args:
        client: The Gemini API client
        model: The model to use for generation
        contents: The contents to generate from
        config: The generation config (if None, uses default config)
        max_retries: Maximum number of retry attempts
        max_backoff: Maximum backoff time in seconds
        operation_name: Name of the operation for logging purposes
        use_streaming: Whether to use streaming mode (default: True)
        
    Returns:
        In non-streaming mode: The complete generated content response
        In streaming mode: A response object with aggregated text from the stream
    """
    retry_count = 0
    response = None
    
    # Use default config if none provided
    if config is None:
        config = get_default_generation_config()
    
    while response is None and retry_count < max_retries:
        try:
            if retry_count > 0:
                # Calculate exponential backoff with jitter
                backoff_time = min(2 ** retry_count + (0.1 * retry_count), max_backoff)
                logger.warning(f"Retry attempt {retry_count} for {operation_name}. Waiting {backoff_time:.2f}s...")
                time.sleep(backoff_time)
            
            if use_streaming:
                # Use streaming mode
                logger.info(f"Using streaming mode for {operation_name}")
                
                # Create a response-like object to store the aggregated content
                class AggregatedResponse:
                    def __init__(self):
                        self.text = ""
                        self.parts = []
                
                aggregated_response = AggregatedResponse()
                
                # Generate content with streaming
                stream_response = client.models.generate_content_stream(
                    model=model,
                    contents=contents,
                    config=config,
                )
                
                # Process the stream
                for chunk in stream_response:
                    if chunk.text:
                        aggregated_response.text += chunk.text
                        # Log progress periodically (every 500 chars)
                        if len(aggregated_response.text) % 500 < 10:
                            logger.debug(f"Streaming progress for {operation_name}: {len(aggregated_response.text)} chars received")
                
                response = aggregated_response
                logger.info(f"Streaming complete for {operation_name}: {len(response.text)} total chars")
            else:
                # Use non-streaming mode (original behavior)
                logger.info(f"Using non-streaming mode for {operation_name}")
                response = client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            
        except httpx.RemoteProtocolError as e:
            logger.error(f"RemoteProtocolError during {operation_name}: {e}")
            retry_count += 1
            continue
            
        except httpx.ReadTimeout as e:
            logger.error(f"ReadTimeout during {operation_name}: {e}")
            retry_count += 1
            continue
            
        except httpx.ConnectTimeout as e:
            logger.error(f"ConnectTimeout during {operation_name}: {e}")
            retry_count += 1
            continue
            
        except httpx.HTTPError as e:
            logger.error(f"HTTPError during {operation_name}: {e}")
            retry_count += 1
            continue
            
        except Exception as e:
            logger.error(f"Unexpected error during {operation_name}: {e}")
            retry_count += 1
            continue
        
        # If we got here with no response, increment retry counter
        if response is None:
            retry_count += 1
    
    if response is None:
        raise ValueError(f"Failed to execute {operation_name} after {max_retries} attempts")
        
    return response


def generate_content_with_retry_non_streaming(client, model, contents, config=None, max_retries=3, max_backoff=30, operation_name="API call"):
    """
    Legacy non-streaming version of generate_content_with_retry for backward compatibility.
    
    Args:
        client: The Gemini API client
        model: The model to use for generation
        contents: The contents to generate from
        config: The generation config (if None, uses default config)
        max_retries: Maximum number of retry attempts
        max_backoff: Maximum backoff time in seconds
        operation_name: Name of the operation for logging purposes
        
    Returns:
        The generated content response
    """
    return generate_content_with_retry(
        client=client,
        model=model,
        contents=contents,
        config=config,
        max_retries=max_retries,
        max_backoff=max_backoff,
        operation_name=operation_name,
        use_streaming=False
    )
