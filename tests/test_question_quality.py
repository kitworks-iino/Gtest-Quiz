import pytest

from gtest_quiz.question_quality import validate_generated_question


def test_valid_question():
    data = {
        "question": "ニューラルネットワークにおける活性化関数の役割は何か。",
        "choices": [
            "線形性を維持する",
            "非線形性を導入する",
            "計算量を削減する",
            "重みを初期化する",
        ],
        "correct_index": 1,
        "explanation": (
            "正解理由: 活性化関数は非線形性を導入し、ニューラルネットワークが複雑な関係を学習できるようにするため重要である。"
            "不正解理由: 線形性維持、計算量削減、重み初期化は活性化関数の中心的な役割ではなく、設問の概念との差分が明確である。"
        ),
        "difficulty": "standard",
    }

    result = validate_generated_question(data)
    assert result.is_valid


def test_invalid_short_explanation():
    data = {
        "question": "AIとは何か。",
        "choices": ["A", "B", "C", "D"],
        "correct_index": 0,
        "explanation": "短い",
        "difficulty": "basic",
    }

    result = validate_generated_question(data)
    assert not result.is_valid


@pytest.mark.parametrize("explanation", [
    "正解理由: 選択肢2は適切です。", "誤答肢（Ａ）は異なります。",
    "最後の選択肢は誤りです。", "選択肢の一つ目は異なります。",
    "不正解理由、3は異なります。他の選択肢: Aは異なります。",
    "正解はインデックス0です。", "インデックス3が正解です。",
])
def test_choice_references_are_detected(explanation):
    from gtest_quiz.question_quality import has_positional_choice_reference
    assert has_positional_choice_reference(explanation)


@pytest.mark.parametrize("explanation", [
    "著作権法第30条の4は、情報解析に関する規定です。",
    "選択肢2つを比較します。", "AI技術に関する説明です。",
    "配列のインデックス0からアクセスします。",
])
def test_technical_numbers_are_not_option_references(explanation):
    from gtest_quiz.question_quality import has_positional_choice_reference
    assert not has_positional_choice_reference(explanation)


def test_incremental_duplicate_index_matches_full_index():
    from gtest_quiz.question_quality import add_question_to_index, build_duplicate_index, is_probable_duplicate
    texts = ["ニューラルネットワークの活性化関数は何か。", "機械学習の汎化性能をどう評価するか。"]
    index = build_duplicate_index([])
    for text in texts:
        add_question_to_index(index, text)
    assert index == build_duplicate_index([{"question": text} for text in texts])
    assert is_probable_duplicate(texts[0].replace("。", "！"), index)
