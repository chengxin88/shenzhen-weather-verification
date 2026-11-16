'use strict';
function activateVerificationModule(name){
 // The standalone station page left the menu on 2026-11-12 (single stations live in the detail pages); old links land on the overview.
 if(name==='stations'){name='overview';history.replaceState(null,'','#overview');}
 if(name==='compare'){location.replace('/Verification/compare/');return;}
 const module=['case','typhoon','monthly','interfaces','quality','stations','overview','nowcast'].includes(name)?name:'grid';
 document.getElementById('overview-workspace').hidden=module!=='overview';
 document.getElementById('nowcast-workspace').hidden=module!=='nowcast';
 document.getElementById('grid-workspace').hidden=module!=='grid';
 document.getElementById('monthly-workspace').hidden=module!=='monthly';
 document.getElementById('case-workspace').hidden=module!=='case';
 document.getElementById('typhoon-workspace').hidden=module!=='typhoon';
 document.getElementById('interfaces-workspace').hidden=module!=='interfaces';
 document.getElementById('quality-workspace').hidden=module!=='quality';
 document.getElementById('stations-workspace').hidden=module!=='stations';
 document.querySelectorAll('[data-module]').forEach(b=>{
  const active=b.dataset.module===module;
  b.setAttribute('aria-pressed',active);b.classList.toggle('active',active);
 });
 document.getElementById('detail-nav')?.classList.toggle('detail-nav-idle',module!=='grid');
 const activeKind=document.getElementById('grid-workspace').dataset.activeKind||'降水';
 document.querySelectorAll('[data-kind]').forEach(b=>b.classList.toggle('active',module==='grid'&&b.dataset.kind===activeKind));
 if(module!=='typhoon')window.NativeTyphoon.close();
 if(module!=='case'){window.NativeCase.close();window.DemoModels.closeCase();}
 if(module!=='monthly')window.MonthlyPrecip.close();
 if(module!=='quality')window.DataQuality.close();
 if(module!=='stations')window.StationVerification.close('stations-workspace');
 if(module!=='overview')window.AssessmentOverview?.close();
 if(module!=='nowcast')window.NowcastRain?.close();
 window.DemoModels.openModule(module);
 if(module==='grid'&&location.hash.includes('ref=station')&&location.hash.includes('element='))window.GridVerification.openOverviewDetail(location.hash);
}
document.querySelectorAll('[data-module]').forEach(b=>b.onclick=()=>{history.replaceState(null,'','#'+b.dataset.module);activateVerificationModule(b.dataset.module);});
document.querySelectorAll('[data-kind]').forEach(b=>b.addEventListener('click',()=>{history.replaceState(null,'','#grid');activateVerificationModule('grid');}));
window.addEventListener('hashchange',()=>activateVerificationModule(location.hash.slice(1).split('&')[0]||'overview'));
document.getElementById('refresh').addEventListener('click',event=>{
 if(!document.getElementById('overview-workspace').hidden){event.stopImmediatePropagation();window.AssessmentOverview.refresh();}
 else if(!document.getElementById('nowcast-workspace').hidden){event.stopImmediatePropagation();window.NowcastRain.refresh();}
 else if(!document.getElementById('stations-workspace').hidden){event.stopImmediatePropagation();window.StationVerification.refresh();}
 else if(!document.getElementById('quality-workspace').hidden){event.stopImmediatePropagation();window.DataQuality.refresh();}
 else if(!document.getElementById('case-workspace').hidden){event.stopImmediatePropagation();window.DemoModels.refreshCase();}
 else if(!document.getElementById('typhoon-workspace').hidden){event.stopImmediatePropagation();window.NativeTyphoon.refresh();}
 else if(!document.getElementById('monthly-workspace').hidden){event.stopImmediatePropagation();window.MonthlyPrecip.refresh();}
 else if(!document.getElementById('interfaces-workspace').hidden){event.stopImmediatePropagation();window.ModelInterfaces.refresh();}
},true);
activateVerificationModule(location.hash.slice(1).split('&')[0]||'overview');
