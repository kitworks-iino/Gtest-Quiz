// This guard checks wording risks, not factual correctness. Historical holds
// were reviewed against the source bank; keep them when new rows are appended.
// The recorded source_bank_hash identifies that audit, not the latest bank.
const HOLD_SCHEMA = 'gtest_question_review_holds_v1';

function hasPositionalChoiceReference(explanation) {
  // Normalize full-width letters/numbers and circled option numbers first.
  const text = String(explanation ?? '').normalize('NFKC');
  const patterns = [
    // Explicit labels: 選択肢2, 選択肢(A), 誤答肢0.
    // Avoid matching AI/algorithm names or quantities such as 選択肢2つ.
    /(?:選択肢|正答肢|誤答肢)\s*(?:の\s*)?(?:\(\s*)?[0-4A-D](?![A-Za-z0-9つ個件])/u,
    // An array index alone is technical content, not necessarily an option.
    /(?:正解|不正解|誤答)(?:は)?\s*(?:\(\s*)?インデックス\s*[0-4](?![0-9])/u,
    /インデックス\s*[0-4](?![0-9])(?:が正解|の選択肢|は誤り)/u,
    /(?:最初|最後|[1-4一二三四](?:つ目|番目))の選択肢/u,
    /選択肢の[1-4一二三四](?:つ目|番目)/u,
    // A bare number/letter is safe to flag only in explicit option context.
    // Global /[1-4]は/ would incorrectly catch 第30条の4は and quantities.
    /(?:他の選択肢|不正解(?:の選択肢)?|誤答)(?:について)?\s*[、,:：]\s*[0-4A-D](?:番目|つ目)?(?:\s*(?:・|、|と|や|および|及び)\s*[0-4A-D](?:番目|つ目)?)*(?=は|も|の|について)/u,
  ];
  return patterns.some(pattern => pattern.test(text));
}

export function partitionQuestions(payload, holds) {
  if (!Array.isArray(payload?.questions)) {
    throw new TypeError('Question bank must contain a questions array.');
  }
  if (holds?.schema_version !== HOLD_SCHEMA || !Array.isArray(holds.ids)
      || holds.ids.some(id => typeof id !== 'string' || !id)) {
    throw new TypeError('Question review holds are missing or invalid.');
  }
  const heldIds = new Set(holds.ids);
  const questions = payload.questions.filter(question =>
    !heldIds.has(question.id) && !hasPositionalChoiceReference(question.explanation));
  return {
    questions,
    heldCount: payload.questions.length - questions.length,
    sourceCount: payload.questions.length,
  };
}
