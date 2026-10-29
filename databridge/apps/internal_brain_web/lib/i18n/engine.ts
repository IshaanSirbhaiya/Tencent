// Strings for the Walnut engine view (/dev/brain). Developer mode may stay English (PLAN §4), but the
// frame of the page (title, banner, lock card, tab names, states) is translated so a 简体中文 session
// reads it in its own language. Engine values (labels, kinds, pointers) are data and stay as sent.
import { defineMessages } from '.';

const en = {
  'engine.eyebrow': 'Observe',
  'engine.title': 'Walnut engine',
  'engine.description': 'The evidence engine underneath DataBridge: the entity graph, the time slider, the provenance of every value down to its row, and the ontology. Read-only.',
  'engine.banner.title': 'Snapshot: Company A as {name} sees it, built {date}.',
  'engine.banner.body': "The Internal Brain's live permission checks apply to answers; this view is the engine's evidence record.",
  'engine.banner.dateUnknown': 'at sync time',
  'engine.banner.readOnly': 'Read-only',
  'engine.banner.mock': 'Mock fixture',
  'engine.lock.title': 'This snapshot is not yours to explore',
  'engine.lock.body': "The engine view is a snapshot built as {name}'s view at sync time. It is not re-checked live and its reads are not logged, so only {name} can open it. Sign in as {name} to explore it.",
  'engine.tab.graph': 'Knowledge graph',
  'engine.tab.brain': 'Company brain',
  'engine.tab.provenance': 'Provenance',
  'engine.tab.ontology': 'Ontology',
  'engine.tabs.label': 'Engine views',
  'engine.down.title': 'The engine is not running',
  'engine.down.body': 'Start the engine, then try again:',
  'engine.off.title': 'The engine view is switched off on this server',
  'engine.off.body': 'It is enabled only on a local demo machine, with WALNUT_PROXY=1 set for the web app.',
  'engine.signedOut.title': 'Sign in to see the engine view',
  'engine.retry': 'Try again',
  'engine.loading': 'Loading the engine',
} as const;

const zh: Record<keyof typeof en, string> = {
  'engine.eyebrow': '观测',
  'engine.title': 'Walnut 引擎',
  'engine.description': 'DataBridge 底层的证据引擎：实体图谱、时间滑块、每个值直到数据行的来源链，以及本体。只读。',
  'engine.banner.title': '快照：{name} 视角下的 Company A，构建于 {date}。',
  'engine.banner.body': 'Internal Brain 的实时权限检查作用于回答；此视图是引擎的证据记录。',
  'engine.banner.dateUnknown': '同步时',
  'engine.banner.readOnly': '只读',
  'engine.banner.mock': '模拟数据',
  'engine.lock.title': '此快照不对您开放',
  'engine.lock.body': '引擎视图是在同步时按 {name} 的视角构建的快照。它不会实时复核权限，查看记录也不写入日志，因此只有 {name} 可以打开。请以 {name} 身份登录后查看。',
  'engine.tab.graph': '知识图谱',
  'engine.tab.brain': '公司大脑',
  'engine.tab.provenance': '来源链',
  'engine.tab.ontology': '本体',
  'engine.tabs.label': '引擎视图',
  'engine.down.title': '引擎未运行',
  'engine.down.body': '请启动引擎后重试：',
  'engine.off.title': '此服务器未开启引擎视图',
  'engine.off.body': '仅在本地演示机上开启，需为网页应用设置 WALNUT_PROXY=1。',
  'engine.signedOut.title': '请登录后查看引擎视图',
  'engine.retry': '重试',
  'engine.loading': '正在加载引擎',
};

export const engineMessages = defineMessages({ en, zh });
export type EngineKey = keyof typeof en;
