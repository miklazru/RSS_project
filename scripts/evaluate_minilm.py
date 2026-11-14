import json
from pathlib import Path

import numpy as np
from sentence_transformers import SentenceTransformer


def evaluate(scores, labels, threshold):
    predicted = scores >= threshold

    tp = int(np.sum(predicted & labels))
    fp = int(np.sum(predicted & ~labels))
    tn = int(np.sum(~predicted & ~labels))
    fn = int(np.sum(~predicted & labels))

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0

    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0
    )

    return {
        "tp": tp,
        "fp": fp,
        "tn": tn,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def evaluate_file(model, path):
    data = json.loads(path.read_text(encoding="utf-8"))

    examples = []
    skipped = 0

    for item in data["examples"]:
        label = item["relevant"]

        if label is None:
            skipped += 1
            continue

        if type(label) is not bool:
            raise ValueError(
                "relevant должен быть true, false или null: "
                f"{item['title']}"
            )

        examples.append(item)

    if not examples:
        raise ValueError(f"Нет размеченных примеров: {path.name}")

    labels = np.array(
        [item["relevant"] for item in examples],
        dtype=bool,
    )

    if not labels.any() or labels.all():
        raise ValueError(
            f"В {path.name} нужны и подходящие, "
            "и неподходящие примеры для подбора порога."
        )

    print(f"\n{'=' * 60}")
    print(f"Тема: {data['topic']}")
    print(f"Файл: {path.name}")
    print(
        f"Примеров: {len(examples)}, "
        f"подходящих: {int(labels.sum())}, "
        f"спорных пропущено: {skipped}"
    )

    # MiniLM принимает обычные тексты без query: и passage:.
    texts = [
        "\n".join(
            part.strip()
            for part in (item["title"], item.get("summary") or "")
            if part and part.strip()
        )
        for item in examples
    ]

    article_vectors = model.encode(
        texts,
        batch_size=16,
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=True,
    ).astype(np.float64)

    topic_vector = model.encode(
        data["topic"],
        normalize_embeddings=True,
        convert_to_numpy=True,
        show_progress_bar=False,
    ).astype(np.float64)

    if (
        not np.isfinite(article_vectors).all()
        or not np.isfinite(topic_vector).all()
    ):
        raise ValueError("Модель вернула NaN или бесконечности")

    # Косинусное сходство нормализованных векторов.
    # Не используем matmul, с которым ранее возникали предупреждения.
    scores = np.sum(article_vectors * topic_vector, axis=1)

    if not np.isfinite(scores).all():
        raise ValueError("Получены некорректные оценки сходства")

    scores = np.clip(scores, -1.0, 1.0)

    print(
        f"\nДиапазон сходства: "
        f"{scores.min():.6f} — {scores.max():.6f}"
    )
    print("\nПорог    TP  FP  FN  Precision  Recall     F1")

    for threshold in (0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        result = evaluate(scores, labels, threshold)

        print(
            f"{threshold:.3f}   "
            f"{result['tp']:3d} "
            f"{result['fp']:3d} "
            f"{result['fn']:3d}    "
            f"{result['precision']:.3f}    "
            f"{result['recall']:.3f}   "
            f"{result['f1']:.3f}"
        )

    # Для сравнения с E5 подбираем порог на известных ответах.
    # Это НЕ независимая проверка качества.
    candidates = sorted(set(float(score) for score in scores))

    def rank(threshold):
        result = evaluate(scores, labels, threshold)
        return (
            result["f1"],
            result["precision"],
            result["recall"],
            threshold,
        )

    best_threshold = max(candidates, key=rank)
    best = evaluate(scores, labels, best_threshold)

    print(f"\nЛучший порог НА ЭТОЙ ВЫБОРКЕ: {best_threshold:.9f}")
    print(
        f"TP={best['tp']}, FP={best['fp']}, "
        f"TN={best['tn']}, FN={best['fn']}"
    )
    print(
        f"Precision={best['precision']:.3f}, "
        f"Recall={best['recall']:.3f}, "
        f"F1={best['f1']:.3f}"
    )

    print("\nОшибки при выбранном пороге:")
    errors = 0

    ranked = sorted(
        zip(examples, scores),
        key=lambda pair: float(pair[1]),
        reverse=True,
    )

    for item, score in ranked:
        predicted = bool(score >= best_threshold)

        if predicted == item["relevant"]:
            continue

        errors += 1
        error = (
            "ПРОПУЩЕНА ПОДХОДЯЩАЯ"
            if item["relevant"]
            else "ОТОБРАНА ЛИШНЯЯ"
        )
        print(f"{error} | {score:.6f} | {item['title']}")

    if errors == 0:
        print("На этой выборке ошибок нет.")


def main():
    root = Path(__file__).resolve().parents[1]
    model_path = root / "models" / "multilingual-minilm"

    if not model_path.is_dir():
        raise SystemExit(
            f"Модель не найдена: {model_path}\n"
            "Сначала дождись завершения команды:\n"
            "python -m scripts.download_minilm"
        )

    print("Загружаем MiniLM с диска...")

    model = SentenceTransformer(
        str(model_path),
        device="cpu",
        local_files_only=True,
    )

    print(f"Максимальная длина входа: {model.max_seq_length} токенов")

    for filename in (
        "topics_labels_ai.json",
        "topics_labels_diplomacy.json",
    ):
        path = root / "data" / filename

        if not path.is_file():
            raise SystemExit(f"Файл разметки не найден: {path}")

        evaluate_file(model, path)

    print(
        "\nЭто результаты подбора на известных примерах. "
        "Они нужны для сравнения моделей, "
        "а не для заявления о качестве на новых статьях."
    )


if __name__ == "__main__":
    main()