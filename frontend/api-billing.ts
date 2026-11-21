// Display-only adapter for the gateway's public pricing expressions.
// The vendored parser never evaluates JavaScript or sends model requests.
import { BILLING_PRICING_VARS, parseTiersFromExpr, getCurrentTimePricingTiers,
  splitBillingExprAndRequestRules } from '../mailapi_shared/static/vendor/billing-expression/source/lib/billing-expr'
import { compileBillingExpression } from '../mailapi_shared/static/vendor/billing-expression/source/lib/billing-expression/parser'
import { formatBillingCondition } from '../mailapi_shared/static/vendor/billing-expression/source/lib/billing-expression/condition-display'
import { getTaskPricingDisplayTiers } from '../mailapi_shared/static/vendor/billing-expression/source/lib/task-matrix-display'
import { evaluateTaskUsageExamples } from '../mailapi_shared/static/vendor/billing-expression/source/lib/task-expr'
export { evaluateBillingExpression as evaluate } from '../mailapi_shared/static/vendor/billing-expression/source/lib/billing-expression/runtime'

const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0
const fieldKeys = { p:'input', c:'output', cr:'cache_read', cc:'cache_write', cc1h:'cache_write_1h',
  cc12h:'cache_write_12h', img:'image', img_cr:'image_cache', img_o:'image_output', ai:'audio_input', ao:'audio_output' }
const labels = { input:'输入', output:'输出', cache_read:'缓存读取', cache_write:'缓存写入',
  cache_write_1h:'缓存写入（1小时）', cache_write_12h:'缓存写入（12小时）', image:'图像输入',
  image_cache:'图像缓存', image_output:'图像输出', audio_input:'音频输入', audio_output:'音频输出',
  request:'每次请求', constant:'附加费用' }
const translated = { Input:'输入', Output:'输出', 'Full input length':'完整输入长度',
  'Audio requests':'音频请求', 'Text-only requests':'纯文本请求', Minute:'分钟', Month:'月份', Day:'日期',
  'Outside these times: {{condition}}':'上述时段以外：{{condition}}',
  '{{first}} and {{second}}':'{{first}} 且 {{second}}', '{{first}} or {{second}}':'{{first}} 或 {{second}}' }
type Entry = { key:string; label:string; unit:string; value:number; schema?:boolean; unitLabel?:unknown; max?:number }

export function condition(source: string, locale = 'zh-CN') {
  const translate = (key: string, options: Record<string, unknown> = {}) => {
    const template = locale.startsWith('zh') ? translated[key] || key : key
    return template.replace(/\{\{(\w+)\}\}/g, (_, name) => String(options[name] ?? ''))
  }
  const formatted = formatBillingCondition(source, translate, locale)
  if (formatted) return formatted.replaceAll(' <= ', ' ≤ ').replaceAll(' >= ', ' ≥ ')
  const compiled = compileBillingExpression(source)
  if (compiled.status !== 'ready') return translate(source)
  const describe = (node: any): string | null => {
    if (node.kind === 'literal') return node.value === null ? 'null' : JSON.stringify(node.value)
    if (node.kind === 'call' && ['param','header'].includes(node.name) && node.args[0]?.kind === 'literal') {
      return `${locale.startsWith('zh') ? node.name === 'param' ? '请求参数' : '请求头' : node.name === 'param' ? 'Request parameter' : 'Header'} ${node.args[0].value}`
    }
    if (node.kind === 'binary' && ['==','!=','<','<=','>','>=','&&','||'].includes(node.operator)) {
      const left = describe(node.left), right = describe(node.right)
      if (left === null || right === null) return null
      const operator = {'==':'=', '!=':'≠', '<=':'≤', '>=':'≥', '&&':locale.startsWith('zh')?'且':'and', '||':locale.startsWith('zh')?'或':'or'}[node.operator] || node.operator
      return ['&&','||'].includes(node.operator) ? `(${left}) ${operator} (${right})` : `${left} ${operator} ${right}`
    }
    return null
  }
  return describe(compiled.ast) || translate(source)
}

function entries(tier: any, schema: any, model: string): Entry[] {
  if ('unitPrices' in tier) {
    const values = Object.entries(schema).flatMap(([key, definition]: [string, any]) => {
      const value = tier.unitPrices[key]
      return definition.unit && finite(value) ? [{key, label:key, unit:definition.unit, unitLabel:definition.unitLabel, value, schema:true}] : []
    })
    if (finite(tier.constant) && (tier.constant > 0 || !values.length)) values.push({key:'constant', label:labels.constant, unit:'request', value:tier.constant})
    return values
  }
  if (tier.billingUnit === 'request') return finite(tier.fixedPrice)
    ? [{key:'request', label:tier.imageCount ? '每张图像' : labels.request, unit:tier.imageCount ? 'image' : 'request', value:tier.fixedPrice}] : []
  return BILLING_PRICING_VARS.flatMap(variable => {
    const key = fieldKeys[variable.key], value = tier[variable.field!]
    if (!key || !finite(value)) return []
    let label = labels[key]
    if (key === 'cache_write') {
      if (/claude/i.test(model) || finite(tier.cacheCreate1hPrice)) label = '缓存写入（5分钟）'
      else if (/gpt/i.test(model) || finite(tier.cacheCreate12hPrice)) label = '缓存写入（30分钟）'
    }
    return [{key, label, unit:'token', value}]
  })
}

function provider(raw: any, model: string, now: Date) {
  const expression = raw.billing_expr || ''
  const { billingExpr } = splitBillingExprAndRequestRules(expression)
  const schema = raw.billing_usage_schema || {}
  const task = Object.keys(schema).length > 0
  const parsed = task ? getTaskPricingDisplayTiers(expression, schema) : parseTiersFromExpr(billingExpr)
  const tiers = parsed.map(tier => ({...tier, entries:entries(tier, schema, model)}))
  const compiled = compileBillingExpression(expression)
  const rules = compiled.status === 'ready' ? compiled.requestRules.map(rule => ({condition:rule.cond, multiplier:rule.multiplier})) : []
  const current = task ? null : getCurrentTimePricingTiers(billingExpr, now)
  const summaryTier = task ? tiers.at(-1) : current?.length ? current[0] : tiers[0]
  let summary = summaryTier ? entries(summaryTier, schema, model) : []
  if (task) summary = summary.map(entry => {
    const prices = tiers.flatMap(tier => tier.entries.filter(value => value.key === entry.key).map(value => value.value))
    return prices.length ? {...entry, value:Math.min(...prices), max:Math.max(...prices)} : entry
  })
  return {key:raw.plugin_key || '', label:raw.plugin_name || '', expression, schema, task, tiers, rules, summary,
    timePricing:Boolean(current), special:!tiers.length,
    examples:evaluateTaskUsageExamples(expression, schema, raw.billing_usage_examples)}
}

export function analyze(row: any, now = new Date()) {
  const raw = row.dynamic_pricing || {}, variants = raw.billing_plugin_variants || []
  const providers = []
  const ordinary = {...raw, plugin_key:'ordinary', plugin_name:variants.length ? '常规调用' : '', ...(variants.length ? {billing_usage_schema:undefined} : {})}
  if (!variants.length || raw.ordinary_pricing) {
    if (raw.billing_expr) providers.push(provider(ordinary, row.id, now))
    else if (raw.ordinary_pricing) providers.push(ratioProvider(ordinary, row))
  }
  for (const variant of variants) providers.push(variant.billing_mode === 'ratio' ? ratioProvider(variant, row) : provider(variant, row.id, now))
  return providers
}

function ratioProvider(raw: any, row: any) {
  const summary = Object.entries(row.prices || {}).map(([key,value]) => ({key, value, label:labels[key], unit:row.base_billing === 'request' ? 'request' : 'token'}))
  return {key:raw.plugin_key, label:raw.plugin_name, expression:'', schema:{}, task:false,
    tiers:summary.length ? [{label:'base', entries:summary, conditions:[]}] : [], rules:[], summary, special:!summary.length, examples:[]}
}

export function summarize(row: any) {
  const values = analyze(row)
  const ordinary = values.find(value => value.key === 'ordinary')
  if (ordinary) return ordinary.summary
  const merged = new Map<string, Entry>()
  for (const value of values) for (const entry of value.summary) {
    const key = `${entry.key}:${entry.unit}`, previous = merged.get(key)
    merged.set(key, previous ? {...entry, value:Math.min(previous.value, entry.value), max:Math.max(previous.max ?? previous.value, entry.max ?? entry.value)} : entry)
  }
  return [...merged.values()]
}

export function tierCondition(tier: any, schema: any, locale = 'zh-CN') {
  const parts = []
  if (tier.conditionText) parts.push(condition(tier.conditionText, locale))
  if ((!tier.conditionText || tier.unitPrices) && tier.conditions?.length) {
    for (const item of tier.conditions) {
      if (item.var) parts.push(condition(`${item.var} ${item.op} ${item.value}`, locale))
      else {
        const label = schema[item.field]?.enumLabels?.[item.value]
        parts.push(`${item.field} = ${typeof label === 'string' ? label : label?.[locale] || label?.[locale.split('-')[0]] || label?.en || item.value}`)
      }
    }
  }
  return parts.join(locale.startsWith('zh') ? ' 且 ' : ' and ')
}
