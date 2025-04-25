import time
import functools
import httpx
from loguru import logger
from google.genai.types import (
    GenerateContentConfig,
    HarmBlockThreshold,
    HarmCategory,
    SafetySetting,
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


def generate_content_with_retry(client, model, contents, config=None, max_retries=3, max_backoff=30, operation_name="API call"):
    """
    Helper function to generate content with retry logic for network-related exceptions.
    
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
            
            # Generate content
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
