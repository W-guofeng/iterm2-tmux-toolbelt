import ast
import json
from pathlib import Path
import re
import shutil
import subprocess
import unittest

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'toolbelt/web/i18n.js').read_text()
CATALOG=json.loads(SOURCE.split('const english = ',1)[1].split(';\nconst preferred',1)[0])
NODE=shutil.which('node') or ('/opt/homebrew/bin/node' if Path('/opt/homebrew/bin/node').exists() else None)

class TranslationCoverageTests(unittest.TestCase):
    def test_backend_messages_have_english_translations(self):
        missing=set()
        for path in (ROOT/'toolbelt').glob('*.py'):
            if path.name=='qa.py': continue
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node,ast.Constant) and isinstance(node.value,str) and re.search('[\u4e00-\u9fff]',node.value) and len(node.value)<500 and node.value not in CATALOG:
                    missing.add(node.value)
        self.assertEqual(missing,set())
        self.assertTrue(all(value.strip() and not re.search('[\u4e00-\u9fff]',value) for value in CATALOG.values()))

    def test_initial_dom_and_dynamic_labels_have_translations(self):
        html=(ROOT/'toolbelt/web/index.html').read_text()
        for key in re.findall(r'data-i18n(?:-[a-z-]+)?="([^"]+)"',html):
            self.assertIn(key,CATALOG)
        app=(ROOT/'toolbelt/web/app.js').read_text()
        for key in re.findall(r'''\bt\(['"]([^'"\n]+)['"]''',app):
            self.assertIn(key,CATALOG)
        self.assertIn('/i18n.js',html)

    @unittest.skipUnless(NODE,'Node needed for language-selection execution')
    def test_system_language_and_user_data_preservation(self):
        code="""
const vm=require('vm'),fs=require('fs');
const source=fs.readFileSync(process.argv[1],'utf8');
const results=[];
for (const langs of [['en-US'],['zh-Hans-CN'],['zh-TW'],['fr-FR','zh-CN'],[]]) {
 const context={navigator:{languages:langs,language:langs[0]},window:{},document:{documentElement:{},querySelectorAll:()=>[]}};
 vm.runInNewContext(source,context);
 const t=context.window.ToolbeltI18n;
 results.push({lang:t.language,label:t.text('删除 {name}',{name:'任务'}),error:t.message('配置结果尚未确认，请刷新检查；不会自动重复写入。'),receipt:t.message('无回显 helper 已配置，请另开 SSH 连接生效。现有任务保留。 备份：/home/删除/.zshrc.backup')});
}
process.stdout.write(JSON.stringify(results));
"""
        result=subprocess.run([NODE,'-e',code,str(ROOT/'toolbelt/web/i18n.js')],capture_output=True,text=True,check=True)
        rows=json.loads(result.stdout)
        self.assertEqual([r['lang'] for r in rows],['en','zh-CN','zh-CN','en','en'])
        self.assertEqual(rows[0]['label'],'Delete 任务')
        self.assertEqual(rows[1]['label'],'删除 任务')
        self.assertNotRegex(rows[0]['error'],'[\u4e00-\u9fff]')
        self.assertTrue(rows[0]['receipt'].endswith('/home/删除/.zshrc.backup'))
