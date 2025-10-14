"""Service for indexing and searching sell scripts."""

import asyncio
import hashlib
from typing import List, Dict, Any

import pandas as pd
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from app.models import SellScript
from app.services.llm_service import get_embedding
from app.services.script_exceptions import ScriptError, ExcelFormatError, IndexingError

log = structlog.get_logger(__name__)


class ScriptService:
    """Manages the indexing and retrieval of sell scripts."""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def index_scripts_from_file(self, file_path: str, sheet_name: str = "scripts") -> Dict[str, int]:
        """
        Reads an Excel file, processes the scripts, and indexes them in the database.
        """
        try:
            df = self._read_and_validate_file(file_path, sheet_name)
            if df.empty:
                log.info("No valid rows found in the script file.", file_path=file_path)
                return {"processed": 0, "added": 0, "updated": 0}

            messages = df["message"].tolist()
            embeddings = await self._generate_embeddings(messages)
            df["embedding"] = embeddings

            scripts_data = []
            for _, row in df.iterrows():
                message_text = str(row["message"])
                answer_text = str(row["answer"])
                row_content = f'{message_text}|{answer_text}'
                scripts_data.append({
                    "sheet": sheet_name,
                    "row_hash": hashlib.sha256(row_content.encode()).hexdigest(),
                    "message": message_text,
                    "answer": answer_text,
                    "embedding": row["embedding"],
                })

            return await self._upsert_scripts(scripts_data)

        except FileNotFoundError:
            log.error("Script file not found.", file_path=file_path)
            raise ScriptError(f"File not found: {file_path}")
        except ExcelFormatError as e:
            log.warning("Excel format error.", error=str(e), file_path=file_path)
            raise
        except Exception as e:
            log.exception("Failed to index scripts from file.", file_path=file_path)
            raise IndexingError(f"An unexpected error occurred during indexing: {e}")

    def _read_and_validate_file(self, file_path: str, sheet_name: str) -> pd.DataFrame:
        """Reads and validates the structure of the Excel file."""
        try:
            df = pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
        except ValueError:
            # Fallback to the first sheet if 'scripts' not found
            try:
                df = pd.read_excel(file_path, sheet_name=0, engine="openpyxl")
                log.info(f"Sheet '{sheet_name}' not found. Using the first available sheet.")
            except Exception as e:
                raise ScriptError(f"Could not read the Excel file or find any sheets. Error: {e}")

        df.columns = df.columns.str.strip()

        if "message" in df.columns and "messenge" in df.columns:
            raise ExcelFormatError("Both 'message' and 'messenge' columns exist. Please use only one.")
        
        if "messenge" in df.columns:
            df = df.rename(columns={"messenge": "message"})
            log.warning("Column 'messenge' found and renamed to 'message'.")

        if "message" not in df.columns or "answer" not in df.columns:
            raise ExcelFormatError("Required columns 'message' and 'answer' are missing.")

        df = df[["message", "answer"]].dropna()
        df = df[df["message"].astype(str).str.strip() != ""]
        df = df[df["answer"].astype(str).str.strip() != ""]
        df["message"] = df["message"].astype(str).str.strip().str.lower()
        df["answer"] = df["answer"].astype(str).str.strip()

        return df.drop_duplicates()

    async def _generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generates embeddings for a list of texts."""
        # This can be parallelized for performance if needed
        tasks = [get_embedding(text) for text in texts]
        embeddings = await asyncio.gather(*tasks)
        return [emb for emb in embeddings if emb is not None]

    async def _upsert_scripts(self, scripts_data: List[Dict[str, Any]]) -> Dict[str, int]:
        """
        Updates existing scripts or inserts new ones based on row hash.
        """
        if not scripts_data:
            return {"processed": 0, "added": 0, "updated": 0}

        stmt = insert(SellScript).values(scripts_data)
        stmt = stmt.on_conflict_do_update(
            index_elements=['row_hash'],
            set_={
                'message': stmt.excluded.message,
                'answer': stmt.excluded.answer,
                'embedding': stmt.excluded.embedding,
                'updated_at': stmt.excluded.updated_at,
            }
        )
        result = await self.session.execute(stmt)
        
        # Note: rowcount is not reliably returned for INSERT...ON CONFLICT in asyncpg
        # We can't easily distinguish between added and updated here.
        # A more complex approach would be needed for precise stats.
        # For now, we count processed rows.
        processed_count = len(scripts_data)
        
        log.info("Upserted scripts.", count=processed_count)
        return {"processed": processed_count, "added": -1, "updated": -1} # -1 indicates unknown

    async def search_similar_scripts(self, query_text: str, top_k: int) -> List[Dict[str, Any]]:
        """
        Searches for the most similar scripts in the database using vector similarity.
        """
        query_embedding = await get_embedding(query_text)
        if not query_embedding:
            log.warning("Could not generate embedding for query.", query=query_text)
            return []

        # The l2_distance operator <-> is used for distance calculation.
        # For cosine similarity, we can use 1 - (embedding <=> query_embedding)
        # Or use the dedicated cosine distance operator <=>.
        # Cosine distance is 1 - cosine_similarity. Lower is better.
        distance_op = SellScript.embedding.cosine_distance(query_embedding)
        
        stmt = (
            select(
                SellScript.id,
                SellScript.message,
                SellScript.answer,
                distance_op.label("distance")
            )
            .order_by(distance_op)
            .limit(top_k)
        )

        result = await self.session.execute(stmt)
        candidates = result.mappings().all()

        # Convert distance to similarity
        return [
            {
                "id": cand["id"],
                "message": cand["message"],
                "answer": cand["answer"],
                "similarity": 1 - cand["distance"]
            }
            for cand in candidates
        ]