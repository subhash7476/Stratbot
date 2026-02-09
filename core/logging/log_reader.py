import os
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional


def tail_log_file(
    file_path: str, 
    lines: int = 300, 
    level_filter: Optional[str] = None, 
    source_filter: Optional[str] = None
) -> List[Dict]:
    """
    Read the last N lines from a log file and parse them into structured data.
    
    Args:
        file_path: Path to the log file
        lines: Number of lines to read from the end
        level_filter: Optional log level to filter (e.g., 'ERROR', 'WARNING')
        source_filter: Optional source/logger name to filter
    
    Returns:
        List of parsed log entries as dictionaries
    """
    if not os.path.exists(file_path):
        return []
    
    # Read the last N lines from the file
    with open(file_path, 'rb') as f:
        # Go to the end of the file
        f.seek(0, os.SEEK_END)
        file_size = f.tell()
        
        # Start from the end and work backwards
        buffer_size = 1024
        lines_found = 0
        blocks = []
        
        while lines_found < lines and file_size > 0:
            # Calculate how much to read
            read_size = min(buffer_size, file_size)
            file_size -= read_size
            
            # Move to position and read block
            f.seek(file_size)
            block = f.read(read_size).decode('utf-8', errors='ignore')
            blocks.append(block)
            
            # Count newlines in this block
            lines_found += block.count('\n')
        
        # Join all blocks and split into lines
        all_text = ''.join(reversed(blocks))
        lines_list = all_text.splitlines()
        
        # Get the last 'lines' entries
        last_lines = lines_list[-lines:]
    
    # Parse each line into structured data
    parsed_entries = []
    for line in last_lines:
        parsed = _parse_log_line(line.strip())
        if parsed:
            # Apply filters if specified
            if level_filter and parsed.get('level') != level_filter:
                continue
            if source_filter and parsed.get('source') != source_filter:
                continue
            parsed_entries.append(parsed)
    
    return parsed_entries


def count_errors(file_path: str, window_minutes: int = 60) -> int:
    """
    Count ERROR entries in a log file within a specified time window.
    
    Args:
        file_path: Path to the log file
        window_minutes: Time window in minutes to look back
    
    Returns:
        Number of ERROR entries in the specified time window
    """
    if not os.path.exists(file_path):
        return 0
    
    cutoff_time = datetime.now() - timedelta(minutes=window_minutes)
    error_count = 0
    
    # Read the file and count recent errors
    with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            parsed = _parse_log_line(line.strip())
            if parsed:
                try:
                    timestamp = datetime.fromisoformat(parsed['timestamp'].replace('Z', '+00:00'))
                    if timestamp >= cutoff_time and parsed.get('level') == 'ERROR':
                        error_count += 1
                except ValueError:
                    # Skip lines with invalid timestamps
                    continue
    
    return error_count


def get_available_log_files() -> List[str]:
    """
    Get a list of all available log files in the logs directory.
    
    Returns:
        List of log file names
    """
    logs_dir = Path("logs")
    if not logs_dir.exists():
        return []
    
    log_files = []
    for file_path in logs_dir.glob("*.log"):
        log_files.append(file_path.name)
    
    return sorted(log_files)


def _parse_log_line(line: str) -> Optional[Dict]:
    """
    Parse a log line using regex to extract timestamp, source, level, and message.
    
    Expected format: YYYY-MM-DD HH:MM:SS,mmm - SOURCE_NAME - LEVEL - MESSAGE
    
    Args:
        line: A single log line
        
    Returns:
        Dictionary with parsed components or None if parsing failed
    """
    # Regex pattern to match the log format: 
    # 2023-01-01 12:00:00,000 - logger_name - LEVEL - message
    pattern = r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - ([\w._]+) - (\w+) - (.*)$'
    
    match = re.match(pattern, line)
    if match:
        timestamp_str, source, level, message = match.groups()
        
        # Convert timestamp to ISO format for consistency
        # Input format: "YYYY-MM-DD HH:MM:SS,mmm"
        dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S,%f")
        iso_timestamp = dt.isoformat()
        
        return {
            'timestamp': iso_timestamp,
            'source': source,
            'level': level,
            'message': message
        }
    
    return None