// User-mode answer components. Compare (and anything else) imports from '@/components/user'.
//
//   <AnswerCard response={res} onCite={(i, el) => …} compact />   verified answer, or the refusal
//   <SourcesSheet response={res} index={i} … />                   right sheet for one cited source
//   <CitationChip citation={c} n={1} onOpen={…} />                inline [n] chip
//   <RefusalCard text={res.answer} />                             the one refusal shape
//   const ask = useAskProgress({ slot: 'b' }); <AskProgressLine step={ask.step} />
export { AnswerCard, AnswerText, ModelLabel, SourceCard, type AnswerCardProps } from './AnswerCard';
export { SourcesSheet, type SourcesSheetProps } from './SourcesSheet';
export { CitationChip } from './CitationChip';
export { RefusalCard } from './RefusalCard';
export { AskProgressLine } from './AskProgressLine';
export { useAskProgress, ASK_STEPS, type AskProgress, type AskPhase, type AskStepKey } from './useAskProgress';
export { AskErrorCard, Composer, ScopePill, StarterCards, MAX_QUESTION, type ComposerHandle } from './Composer';
export { Toaster, toast } from './Toast';
export { isRefusal } from './answerText';
