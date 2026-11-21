import unittest

from mailapi_shared.api_catalog import catalog, performance_details


class ModelCatalogTests(unittest.TestCase):
    def pricing(self, row):
        return {'data':[row],'group_ratio':{'default':1,'budget':.5,'private':9},
                'vendors':[{'id':1,'name':'Vendor'}],
                'supported_endpoint':{'openai':{'path':'/v1/chat/completions','method':'POST'},
                                      'unsafe':{'path':'/v1/../api/user','method':'GET'}}}

    def test_prices_use_native_ratios_and_only_allowed_groups_and_models(self):
        raw={'model_name':'one','vendor_id':1,'quota_type':0,'model_ratio':2,'completion_ratio':3,
             'cache_ratio':.2,'create_cache_ratio':1.25,'enable_groups':['all'],
             'supported_endpoint_types':['openai','unsafe'],'tags':'text, tools'}
        result=catalog(['one','hidden'],{'default':'','budget':''},self.pricing(raw),{},None,['one'])
        self.assertEqual([item['id'] for item in result['items']],['one'])
        row=result['items'][0]
        self.assertEqual(row['prices'],{'input':4,'output':12,'cache_read':.8,'cache_write':5})
        self.assertEqual(row['tags'],['text','tools'])
        self.assertEqual(row['groups'],[{'id':'default','ratio':1},{'id':'budget','ratio':.5}])
        self.assertEqual([value['id'] for value in row['endpoints']],['openai'])

    def test_dynamic_prices_are_not_misrepresented_as_fixed_or_free(self):
        raw={'model_name':'dynamic','quota_type':0,'model_ratio':0,'completion_ratio':0,
             'billing_mode':'tiered_expr','billing_expr':'not evaluated','enable_groups':['default']}
        row=catalog(['dynamic'],{'default':''},self.pricing(raw),{},None,[])['items'][0]
        self.assertEqual(row['billing'],'dynamic')
        self.assertEqual(row['prices'],{})
        raw.update(model_name='free',billing_mode='ratio',billing_expr='')
        row=catalog(['free'],{'default':''},self.pricing(raw),{},None,[])['items'][0]
        self.assertEqual(row['prices'],{'input':0,'output':0})

    def test_dynamic_contract_preserves_rules_and_public_task_fields_without_raw_admin_data(self):
        expression='len <= 200000 ? tier("base", p * 1.25 + c * 10) : tier("long", p * 2.5 + c * 15)'
        raw={'model_name':'dynamic','quota_type':0,'billing_mode':'tiered_expr','billing_expr':expression,
             'enable_groups':['default'],'private_config':'secret',
             'billing_plugin_variants':[{'plugin_key':'video','plugin_name':'Video','billing_expr':'tier("base", u("duration") * 0.2)',
               'credential':'secret','billing_usage_schema':{'duration':{'type':'number','unit':'second','credential':'secret'}},
               'billing_usage_examples':[{'label':'10 seconds','facts':{'duration':10,'secret':'credential'}}]}]}
        pricing=self.pricing(raw);pricing['auto_groups']=['private','default']
        row=catalog(['dynamic'],{'default':''},pricing,{},None,[])['items'][0]
        self.assertEqual(row['dynamic_pricing']['billing_expr'],expression)
        variant=row['dynamic_pricing']['billing_plugin_variants'][0]
        self.assertEqual(variant['billing_usage_schema'],{'duration':{'type':'number','unit':'second'}})
        self.assertEqual(variant['billing_usage_examples'][0]['facts'],{'duration':10})
        self.assertNotIn('credential',variant)
        self.assertNotIn('private_config',row)
        self.assertEqual(row['auto_groups'],['default'])

    def test_request_price_and_configured_recharge_currency_have_distinct_rates(self):
        raw={'model_name':'image','quota_type':1,'model_price':.02,'enable_groups':['default']}
        result=catalog(['image'],{'default':''},self.pricing(raw),
                       {'quota_display_type':'CNY','usd_exchange_rate':7,'price':4},None,[])
        self.assertEqual(result['items'][0]['prices'],{'request':.02})
        self.assertEqual(result['currency'],{'symbol':'¥','standard_rate':7,'recharge_rate':4})
        missing=catalog(['image'],{'default':''},None,None,None,[])
        self.assertFalse(missing['pricing_available'])
        self.assertFalse(missing['performance_available'])
        self.assertEqual(missing['items'][0]['prices'],{})
        self.assertIsNone(missing['currency']['recharge_rate'])

    def test_performance_preserves_missing_values_and_filters_private_groups_and_old_buckets(self):
        data={'model_name':'one','summary':{'success_rate':101,'avg_tps':float('inf'),'avg_latency_ms':0},
              'groups':[{'group':'default','avg_tps':2,'secret':'hidden'},{'group':'private','avg_tps':10}],
              'series':[{'ts':1600000000,'success_rate':0},{'ts':1600000100,'success_rate':99}]}
        result=performance_details(data,'one',['default'])
        self.assertEqual([group['id'] for group in result['groups']],['default'])
        self.assertIsNone(result['summary']['success_rate'])
        self.assertIsNone(result['summary']['avg_tps'])
        self.assertEqual(result['summary']['avg_latency_ms'],0)
        self.assertIsNone(performance_details(data,'other',['default']))
        summaries={'window_start':1600000050,'window_end':1600000200,'models':[
            {'model_name':'one','recent_success_series':data['series']}]}
        row=catalog(['one'],{},None,None,summaries,[])['items'][0]
        self.assertEqual([point['ts'] for point in row['recent']],[1600000100])
