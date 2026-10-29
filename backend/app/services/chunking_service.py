def split_text(
    text: str,
    chunk_size: int = 100,
    overlap: int = 20
) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("Chunk size must be greater than zero.")

    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("Overlap must be between 0 and chunk_size - 1.")

    words = text.split()
    chunks = []
    step = chunk_size - overlap

    for start in range(0, len(words), step):
        chunk = " ".join(words[start:start + chunk_size])
        chunks.append(chunk)

        if start + chunk_size >= len(words):
            break

    return chunks