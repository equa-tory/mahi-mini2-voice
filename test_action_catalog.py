import unittest
from unittest.mock import patch
from action_catalog import catalog, validate_catalog, voice_routes, response
from voice_intents import classify

class CatalogTests(unittest.TestCase):
    def test_legacy_import_preserves_names_phrases_and_templates(self):
        old={'aliases':{'включи компьютер':'server_hello'},'intent_triggers':{'timer_start':['засеки']},'responses':{'timer_start':'Ждать {duration}.','action':'{result_state}'}}
        entries=catalog(old,{'face':{}},{'server_hello':{}})
        self.assertEqual(entries['builtin:timer_start']['phrases'],['засеки'])
        self.assertEqual(entries['builtin:timer_start']['responses'],['Ждать {duration}.'])
        self.assertEqual(entries['server:server_hello']['phrases'],['включи компьютер'])
        self.assertEqual(entries['server:server_hello']['responses'],['{result_state}'])
        self.assertEqual(old['aliases'],{'включи компьютер':'server_hello'})

    def test_random_variants_and_placeholder_validation(self):
        config={'action_catalog':{'builtin:timer_start':{'phrases':['засеки'],'responses':['Ждать {duration}.','{duration} осталось.']}}}
        validate_catalog(config['action_catalog'])
        with patch('action_catalog.random.choice',side_effect=lambda lines:lines[-1]):
            self.assertEqual(response(config,'timer_start',duration='две минуты'),'две минуты осталось.')
        for text in ('{__class__}','{duration.real}','{duration!r}','{duration:03d}'):
            with self.assertRaises(ValueError):validate_catalog({'builtin:timer_start':{'responses':[text]}})
        with self.assertRaises(ValueError):validate_catalog({'server:hello':{'responses':['{result_state}']}},{'hello':{'results':{}}})

    def test_named_phrases_and_disabling_legacy_routes(self):
        config={'aliases':{'привет':'hello'},'action_catalog':{'server:hello':{'phrases':[],'responses':['']},'speaker:face':{'phrases':['покажи лицо'],'responses':['Ладно.']}}}
        aliases,triggers=voice_routes(config)
        self.assertEqual(classify('привет',aliases,triggers),('unknown',None))
        self.assertEqual(classify('лицо покажи',aliases,triggers),('action','speaker:face'))

    def test_shared_phrase_is_ambiguous(self):
        entries={target+':demo':{'phrases':['демо'],'responses':['']} for target in ('speaker','server')}
        self.assertEqual(classify('демо',*voice_routes({'action_catalog':entries})),('ambiguous',None))

    def test_custom_duration_validation(self):
        for seconds in (0,-1,True,86401,'3'):
            with self.assertRaises(ValueError):validate_catalog({'speaker:face':{'responses':[''],'display_seconds':seconds}})
