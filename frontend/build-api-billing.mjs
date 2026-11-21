import {build} from 'esbuild';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import fs from 'node:fs/promises';
import {createHash} from 'node:crypto';

const here=path.dirname(fileURLToPath(import.meta.url));
await build({absWorkingDir:here,entryPoints:['api-billing.ts'],bundle:true,format:'iife',
  globalName:'MilvlgBilling',target:'es2022',minify:true,legalComments:'inline',
  footer:{js:'window.MilvlgBilling = MilvlgBilling;'},
  outfile:path.join(here,'../mailapi_shared/static/vendor/billing-expression/billing.js')});
const vendor=path.join(here,'../mailapi_shared/static/vendor');
const manifestPath=path.join(vendor,'manifest.json');
const manifest=JSON.parse(await fs.readFile(manifestPath,'utf8')).filter(item=>item.package!=='billing-expression');
const hashes={};
async function hashFiles(directory,prefix=''){
  for(const entry of (await fs.readdir(directory,{withFileTypes:true})).sort((a,b)=>a.name.localeCompare(b.name))){
    const name=prefix+entry.name;
    if(entry.isDirectory())await hashFiles(path.join(directory,entry.name),name+'/');
    else hashes[name]=createHash('sha256').update(await fs.readFile(path.join(directory,entry.name))).digest('hex');
  }
}
await hashFiles(path.join(vendor,'billing-expression'));
manifest.push({package:'billing-expression',version:'design-reference-2026-11-21',license:'AGPL-3.0-or-later',
  source:'frontend/api-billing.ts',sha256:hashes});
await fs.writeFile(manifestPath,JSON.stringify(manifest,null,2)+'\n');
