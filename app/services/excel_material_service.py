"""
Service for managing materials from Excel files.
Handles reading, validation, and progress tracking without database interaction.
"""

import csv
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import List, Dict, Optional, Tuple

import pandas as pd
from filelock import FileLock

# Configure logging
logger = logging.getLogger(__name__)

# Define paths relative to the sell/ directory
BASE_DATA_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'data'))
EXCEL_FILE_PATH = os.path.join(BASE_DATA_PATH, 'materials.xlsx')
MEDIA_PATH = os.path.join(BASE_DATA_PATH, 'media')
PROGRESS_FILE_PATH = os.path.join(BASE_DATA_PATH, 'materials_progress.json')
LOG_FILE_PATH = os.path.join(BASE_DATA_PATH, 'materials_send_log.csv')
CONFIG_FILE_PATH = os.path.join(BASE_DATA_PATH, 'materials_schedule_config.json')


@dataclass
class Material:
    """Represents one row from the Excel file."""
    row_index: int
    title: str
    text: str
    media_filename: str
    media_path: str
    media_type: str  # 'photo' or 'video'


@dataclass
class ValidationResult:
    """Summary of the Excel file validation."""
    total_rows: int
    valid_rows: int
    skipped_rows: int
    reasons: Dict[str, int]
    
    @property
    def is_valid(self) -> bool:
        return self.valid_rows > 0


class ExcelMaterialService:
    """
    Manages the lifecycle of materials stored in an Excel file.
    This service is designed to be DB-free.
    """

    def __init__(self):
        # Ensure data directory and necessary files exist
        os.makedirs(BASE_DATA_PATH, exist_ok=True)
        os.makedirs(MEDIA_PATH, exist_ok=True)
        if not os.path.exists(PROGRESS_FILE_PATH):
            with open(PROGRESS_FILE_PATH, 'w', encoding='utf-8') as f:
                json.dump({}, f)
        if not os.path.exists(LOG_FILE_PATH):
            with open(LOG_FILE_PATH, 'w', encoding='utf-8', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(['ts_utc', 'user_id', 'username', 'row', 'title', 'media_filename', 'status', 'error'])
        if not os.path.exists(CONFIG_FILE_PATH):
             with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
                json.dump({
                    'frequency': 'daily_1',
                    'paused': False,
                    'window_start_h_msk': 11,
                    'window_end_h_msk': 20,
                }, f, indent=4)

    def get_materials_dataframe(self) -> Optional[pd.DataFrame]:
        """Reads the Excel file into a pandas DataFrame."""
        try:
            if not os.path.exists(EXCEL_FILE_PATH):
                logger.warning(f"Materials Excel file not found at {EXCEL_FILE_PATH}")
                return None
            
            df = pd.read_excel(EXCEL_FILE_PATH, sheet_name='materials', engine='openpyxl')
            
            # Basic cleanup
            df.dropna(how='all', inplace=True)
            df = df.apply(lambda x: x.str.strip() if x.dtype == "object" else x)
            
            return df
        except Exception as e:
            logger.error(f"Failed to read or process Excel file: {e}", exc_info=True)
            return None

    def validate_excel_file(self) -> Optional[ValidationResult]:
        """Validates the Excel file structure and content."""
        df = self.get_materials_dataframe()
        if df is None:
            return None

        total_rows = len(df)
        valid_rows = 0
        skipped_rows = 0
        reasons = {}

        required_columns = ['title', 'text', 'media_filename']
        if not all(col in df.columns for col in required_columns):
            logger.error(f"Excel file is missing one of the required columns: {required_columns}")
            return None

        for _, row in df.iterrows():
            reason = None
            if pd.isna(row['media_filename']) or not row['media_filename']:
                reason = "empty_media_filename"
            else:
                media_path = os.path.join(MEDIA_PATH, row['media_filename'])
                if not os.path.exists(media_path):
                    reason = "media_file_not_found"
            
            if reason:
                skipped_rows += 1
                reasons[reason] = reasons.get(reason, 0) + 1
            else:
                valid_rows += 1
        
        return ValidationResult(total_rows, valid_rows, skipped_rows, reasons)

    def get_next_material_for_user(self, user_id: int) -> Optional[Material]:
        """
        Gets the next material for a user based on their progress.
        Handles circular iteration through the material list.
        """
        df = self.get_materials_dataframe()
        if df is None or df.empty:
            return None

        progress_data = self._read_progress_file()
        user_progress = progress_data.get(str(user_id), {"last_row": 0})
        last_row_index = user_progress.get("last_row", 0)
        
        next_row_index = last_row_index
        
        for i in range(len(df)):
            current_row_to_check = (next_row_index + i) % len(df)
            row = df.iloc[current_row_to_check]
            
            media_filename = row.get('media_filename')
            if pd.isna(media_filename) or not media_filename:
                continue

            media_path = os.path.join(MEDIA_PATH, media_filename)
            if not os.path.exists(media_path):
                continue

            # Found a valid row
            file_extension = os.path.splitext(media_filename)[1].lower()
            video_extensions = ['.mp4', '.mov', '.webm']
            media_type = 'video' if file_extension in video_extensions else 'photo'
            
            return Material(
                row_index=current_row_to_check + 1, # 1-based for logs
                title=row.get('title'),
                text=row.get('text'),
                media_filename=media_filename,
                media_path=media_path,
                media_type=media_type
            )
            
        logger.warning("No valid materials found in the Excel file after a full loop.")
        return None

    def update_user_progress(self, user_id: int, row_index: int):
        """Updates the user's progress after a successful send."""
        with FileLock(f"{PROGRESS_FILE_PATH}.lock"):
            progress_data = self._read_progress_file()
            progress_data[str(user_id)] = {
                "last_row": row_index,
                "last_sent_at": datetime.utcnow().isoformat()
            }
            self._write_progress_file(progress_data)

    def log_send_attempt(self, user_id: int, username: Optional[str], material: Material, status: str, error: str = ""):
        """Logs a material sending attempt to the CSV file."""
        log_entry = [
            datetime.utcnow().isoformat(),
            user_id,
            username or "",
            material.row_index,
            material.title,
            material.media_filename,
            status,
            error
        ]
        with open(LOG_FILE_PATH, 'a', encoding='utf-8', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(log_entry)

    def _read_progress_file(self) -> Dict:
        try:
            with open(PROGRESS_FILE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}

    def _write_progress_file(self, data: Dict):
        with open(PROGRESS_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)

    def get_schedule_config(self) -> Dict:
        """Reads the schedule configuration."""
        try:
            with open(CONFIG_FILE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            # Return default config if file is missing or corrupt
            return {
                'frequency': 'daily_1',
                'paused': False,
                'window_start_h_msk': 11,
                'window_end_h_msk': 20,
            }

    def save_schedule_config(self, config: Dict):
        """Saves the schedule configuration."""
        with open(CONFIG_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)

    def get_latest_log_entries(self, limit: int = 20) -> List[Dict]:
        """Retrieves the last N entries from the send log."""
        try:
            with open(LOG_FILE_PATH, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                return list(reader)[-limit:]
        except FileNotFoundError:
            return []
        except Exception as e:
            logger.error(f"Error reading log file: {e}")
            return []

# Singleton instance
excel_material_service = ExcelMaterialService()