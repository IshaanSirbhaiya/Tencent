// Voice dictation strings for the Ask composer, English + 简体中文.
import { defineMessages } from '.';

export const voiceMessages = defineMessages({
  en: {
    'voice.start': 'Start dictation',
    'voice.stop': 'Stop dictating',
    'voice.listening': 'Listening…',
    'voice.stopped': 'Stopped',
    'voice.denied': 'Microphone blocked. Allow it in your browser, or type your question.',
    'voice.error': 'Dictation stopped. You can type your question instead.',
    'voice.note': 'Dictation uses your browser’s speech service. Review the text before asking.',
  },
  zh: {
    'voice.start': '开始语音输入',
    'voice.stop': '停止语音输入',
    'voice.listening': '正在聆听…',
    'voice.stopped': '已停止',
    'voice.denied': '麦克风已被阻止。请在浏览器中允许，或直接输入问题。',
    'voice.error': '语音输入已停止。你可以直接输入问题。',
    'voice.note': '语音输入使用你浏览器的语音服务。提问前请检查文字。',
  },
});

/** Recognition language follows the UI language. */
export const DICTATION_LANG = { en: 'en-SG', zh: 'zh-CN' } as const;
