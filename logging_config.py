import os
from pathlib import Path
from loguru import logger


def configure_logging(title=None):
    """
    Configure loguru logger to write logs to stderr and to a file in output/title/logs/ folder.
    
    Args:
        title (str, optional): The book title to use for the log folder. If None, logs will only go to stderr.
    
    Returns:
        The configured logger instance
    """
    # If title is provided, add file handler
    if title:
        # Create logs directory
        log_dir = Path("output") / title / "logs"
        os.makedirs(log_dir, exist_ok=True)
        
        # Add file handler
        log_file = log_dir / "process.log"
        logger.add(
            sink=str(log_file),
            format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
            level="DEBUG",
            rotation="10 MB",
            retention="1 week",
        )
    
    return logger
