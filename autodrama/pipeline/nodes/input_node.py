"""Pipeline node: document input — reads .docx as raw script."""

from __future__ import annotations

from pathlib import Path

from docx import Document

from autodrama.pipeline.state import DramaState
from autodrama.utils.logger import logger


class InputNode:
    """Read a .docx document and load its plain text into the pipeline state.

    Sets ``raw_script`` and ``concept`` from the document body.
    """

    def execute(self, state: DramaState) -> dict:
        docx_path = state.get("docx_path", "").strip()

        if not docx_path:
            logger.error("No docx_path provided in state")
            return {
                "errors": [
                    {
                        "stage": "input_document",
                        "error": "No docx_path provided",
                        "retry_count": 99,
                    }
                ],
                "current_stage": "input_document",
            }

        path = Path(docx_path)
        if not path.exists():
            logger.error(f"File not found: {docx_path}")
            return {
                "errors": [
                    {
                        "stage": "input_document",
                        "error": f"File not found: {docx_path}",
                        "retry_count": 99,
                    }
                ],
                "current_stage": "input_document",
            }

        if path.suffix.lower() != ".docx":
            logger.warning(f"Expected .docx, got: {path.suffix}")

        doc = Document(str(path))
        paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
        full_text = "\n".join(paragraphs)

        logger.info(f"Loaded docx: {len(full_text)} chars, {len(paragraphs)} paragraphs from {docx_path}")

        return {
            "concept": full_text,
            "raw_script": full_text,
            "docx_path": docx_path,
            "current_stage": "input_document",
            "errors": [],
        }
