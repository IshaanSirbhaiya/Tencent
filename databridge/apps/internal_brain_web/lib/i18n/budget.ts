// Strings for the stand-in model's guard ("Model budget", D-42): numbers only, shown to admin and
// compliance (/ib/status) and on the preflight board. English + 简体中文. Never a provider name.
import { defineMessages } from '.';

const budgetEn = {
  'budget.label': 'Model budget',
  'budget.spent': '{spent} of {cap} spent',
  'budget.today': 'today {today} of {daily}',
  'budget.calls': '{n} calls',
  'budget.capReached': 'a cap is reached: answers are quoted, pages use the rules',
  'budget.closed': 'the spend ledger cannot be read: no model calls, answers are quoted',
};

const budgetZh: Record<keyof typeof budgetEn, string> = {
  'budget.label': '模型预算',
  'budget.spent': '已用 {spent}，上限 {cap}',
  'budget.today': '今日 {today}，每日上限 {daily}',
  'budget.calls': '{n} 次调用',
  'budget.capReached': '已达上限：回答改为直接引用来源，页面按规则生成',
  'budget.closed': '无法读取用量记录：不调用模型，回答改为直接引用来源',
};

export const budgetMessages = defineMessages({ en: budgetEn, zh: budgetZh });
export type BudgetKey = keyof typeof budgetEn;
