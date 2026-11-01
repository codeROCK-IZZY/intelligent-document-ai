# Intelligent Document AI

Upload documents (PDF, Word, text, scans), and the platform extracts, classifies and summarises them. Ask questions in plain English and get answers **grounded in your documents, with citations to the exact page and passage**. When the documents don't contain the answer, it says so.

**Status:** design phase. See [`docs/DESIGN.md`](docs/DESIGN.md); implementation starts after owner approval.

Planned stack: Python · FastAPI · PostgreSQL + pgvector · Tesseract OCR · local embeddings · Claude API (native citations) · React + TypeScript · Docker · Terraform

- Design: [`docs/DESIGN.md`](docs/DESIGN.md)
- Engineering standards: [`CLAUDE.md`](CLAUDE.md)
