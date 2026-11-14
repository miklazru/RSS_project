from pathlib import Path

from sentence_transformers import SentenceTransformer


def main():
    root = Path(__file__).resolve().parents[1]
    target = root / "models" / "multilingual-minilm"

    model = SentenceTransformer(
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        device="cpu",
    )
    model.save(str(target))

    print(f"Модель сохранена: {target}")


if __name__ == "__main__":
    main()