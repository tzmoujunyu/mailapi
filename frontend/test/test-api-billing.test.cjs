const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const scope={window:{},Intl,Date};
vm.runInNewContext(fs.readFileSync(path.resolve(__dirname,'../../mailapi_shared/static/vendor/billing-expression/billing.js'),'utf8'),scope);
const billing=scope.window.MilvlgBilling;
const row=(expression,extra={})=>({id:'claude-test',billing:'dynamic',dynamic_pricing:{billing_mode:'tiered_expr',billing_expr:expression,...extra}});
const expression='(len <= 200000 ? tier("base", p * 1.25 + c * 10 + cr * 0.125 + cc * 1.5625 + cc1h * 2.5) : tier("long", p * 2.5 + c * 15 + cr * 0.25 + cc * 3.125 + cc1h * 5)) * (param("service_tier") == "priority" ? 1.5 : 1)';

test('dynamic tables retain every tier, cache column and request multiplier; summaries use first tier',()=>{
  const [value]=billing.analyze(row(expression));
  assert.equal(value.tiers.length,2);assert.equal(value.summary[0].value,1.25);
  assert.equal(value.tiers[1].entries.find(entry=>entry.key==='cache_write_1h').value,5);
  assert.equal(value.rules[0].multiplier,1.5);
  assert.match(billing.tierCondition(value.tiers[0],{},'en'),/Full input length ≤ 200,000/);
  assert.match(billing.condition(value.rules[0].condition,'en'),/Request parameter service_tier = "priority"/);
  for(const [input,tier,output,multiplier] of [[200000,'base',10,1],[200001,'long',15,1],[200001,'long',15,1.5]]){
    const result=billing.evaluate(expression,{tokens:{p:input,len:input,c:5,cr:0,cc:0,cc1h:0},request:{body:{service_tier:multiplier===1.5?'priority':'standard'}}});
    assert.equal(result.status,'success');assert.equal(result.matchedTier,tier);
    assert.equal(result.cost,((input===200000?1.25:2.5)*input+5*output)*multiplier);
  }
});

test('time pricing shows all branches but card summary follows the current time without replacing detail rows',()=>{
  const expr='hour("UTC") < 12 ? (len < 10 ? tier("small", p * 2 + c * 4) : tier("large", p * 3 + c * 5)) : tier("evening", p * 1 + c * 2)';
  const [morning]=billing.analyze(row(expr),new Date('2026-11-21T01:00:00Z'));
  const [evening]=billing.analyze(row(expr),new Date('2026-11-21T18:00:00Z'));
  assert.equal(morning.tiers.length,3);assert.equal(evening.tiers.length,3);
  assert.equal(morning.summary[0].value,2);assert.equal(evening.summary[0].value,1);
  assert.match(billing.tierCondition(evening.tiers[2],{},'zh-CN'),/上述时段以外/);
});

test('task enum tiers retain seconds and token dimensions and a unique fallback condition',()=>{
  const schema={resolution:{enum:['720p','1080p']},duration:{type:'number',unit:'second'},tokens:{type:'number',unit:'token'}};
  const [value]=billing.analyze(row('u("resolution") == "1080p" ? tier("HD", u("duration") * 0.2 + u("tokens") * 2 / 1000000) : tier("SD", u("duration") * 0.1 + u("tokens") * 1 / 1000000)',{billing_usage_schema:schema,
    billing_usage_examples:[{label:'HD sample',facts:{resolution:'1080p',duration:10,tokens:100}}]}));
  assert.equal(value.tiers.length,2);
  assert.equal(value.tiers.find(tier=>tier.label==='SD').conditions[0].value,'720p');
  assert.equal(value.summary.find(entry=>entry.key==='duration').max,.2);
  assert.equal(value.tiers[0].entries.find(entry=>entry.key==='tokens').unit,'token');
  assert.ok(Math.abs(value.examples[0].total-2.0002)<1e-10);
});

test('per-image fixed prices, explicit zeros and mixed provider prices keep their billing dimensions',()=>{
  const [value]=billing.analyze(row('image_count * tier("image", fixed(0))'));
  assert.equal(value.summary[0].unit,'image');assert.equal(value.summary[0].value,0);
  const multi={id:'test',billing:'dynamic',prices:{input:2,output:8},base_billing:'token',dynamic_pricing:{ordinary_pricing:true,billing_plugin_variants:[
    {plugin_key:'one',plugin_name:'One',billing_expr:'tier("base", u("seconds") * 0.5)',billing_usage_schema:{seconds:{type:'number',unit:'second'}}}]}};
  assert.equal(billing.analyze(multi).length,2);assert.equal(billing.summarize(multi)[0].value,2);
  delete multi.dynamic_pricing.ordinary_pricing;
  multi.dynamic_pricing.billing_plugin_variants.push({...multi.dynamic_pricing.billing_plugin_variants[0],plugin_key:'two',billing_expr:'tier("base", u("seconds") * 1)'});
  assert.equal(billing.summarize(multi)[0].value,.5);assert.equal(billing.summarize(multi)[0].max,1);
});

test('nonlinear, malicious, incomplete and oversized expressions never become partial or free prices',()=>{
  for(const expr of ['max(p * 2, 100)','globalThis.compromised = true','tier("bad", p * 2 + unknown(3))','a'.repeat(70000)]){
    const [value]=billing.analyze(row(expr));
    assert.equal(value.special,true);assert.equal(value.tiers.length,0);assert.equal(value.summary.length,0);
  }
  assert.equal(scope.compromised,undefined);
});
