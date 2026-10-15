'use strict';
function activateVerificationModule(name){
 if(name==='compare'){location.replace('/Verification/compare/');return;}
 const module=['case','typhoon','monthly','interfaces','quality'].includes(name)?name:'grid';
 document.getElementById('grid-workspace').hidden=module!=='grid';
 document.getElementById('monthly-workspace').hidden=module!=='monthly';
 document.getElementById('case-workspace').hidden=module!=='case';
 document.getElementById('typhoon-workspace').hidden=module!=='typhoon';
 document.getElementById('interfaces-workspace').hidden=module!=='interfaces';
 document.getElementById('quality-workspace').hidden=module!=='quality';
 document.querySelectorAll('[data-module]').forEach(b=>{
  const active=b.dataset.module===module;
  b.setAttribute('aria-pressed',active);b.classList.toggle('active',active);
 });
 const activeKind=document.getElementById('grid-workspace').dataset.activeKind||'降水';
 document.querySelectorAll('[data-kind]').forEach(b=>b.classList.toggle('active',module==='grid'&&b.dataset.kind===activeKind));
 if(module!=='typhoon')window.NativeTyphoon.close();
 if(module!=='case'){window.NativeCase.close();window.DemoModels.closeCase();}
 if(module!=='monthly')window.MonthlyPrecip.close();
 if(module!=='quality')window.DataQuality.close();
 window.DemoModels.openModule(module);
}
document.querySelectorAll('[data-module]').forEach(b=>b.onclick=()=>{activateVerificationModule(b.dataset.module);history.replaceState(null,'','#'+b.dataset.module);});
document.querySelectorAll('[data-kind]').forEach(b=>b.addEventListener('click',()=>{activateVerificationModule('grid');history.replaceState(null,'','#grid');}));
window.addEventListener('hashchange',()=>activateVerificationModule(location.hash.slice(1)));
document.getElementById('refresh').addEventListener('click',event=>{
 if(!document.getElementById('quality-workspace').hidden){event.stopImmediatePropagation();window.DataQuality.refresh();}
 else if(!document.getElementById('case-workspace').hidden){event.stopImmediatePropagation();window.DemoModels.refreshCase();}
 else if(!document.getElementById('typhoon-workspace').hidden){event.stopImmediatePropagation();window.NativeTyphoon.refresh();}
 else if(!document.getElementById('monthly-workspace').hidden){event.stopImmediatePropagation();window.MonthlyPrecip.refresh();}
 else if(!document.getElementById('interfaces-workspace').hidden){event.stopImmediatePropagation();window.ModelInterfaces.refresh();}
},true);
activateVerificationModule(location.hash.slice(1));
