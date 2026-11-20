'use strict';
// 无第三方依赖：执行真实地图渲染器，核验旧区域隔离与全域范围。
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const window={matchMedia:()=>({matches:false})};
vm.runInNewContext(fs.readFileSync('stations.js','utf8'),{window,document:{},Intl,Date,console});
const base=JSON.parse(fs.readFileSync('station-basemap.json','utf8'));
const stations=[{id:'old',name:'旧站',lon:114,lat:23,type:'synop'},
 {id:'new-inside',name:'框内扩展',lon:115,lat:24,type:'synop',cmagd_extended:true},
 {id:'new-north',name:'北方扩展',lon:116,lat:40,type:'synop',cmagd_extended:true}];
const metadata={...base,stations,variables:[{id:'t',label:'气温',unit:'℃'}]},state={var:'t',model:'gfs',metric:'RMSE'};
const rows=stations.map(s=>({station:s.id,RMSE:1,n:10}));
const old=window.StationVisuals.map(metadata,state,rows,'GFS',{k:1});
const wide=window.StationVisuals.map(metadata,state,rows,'GFS',{k:1,base:'cmagd'});
assert(old.includes('data-station-id="old"'));
assert(!old.includes('data-station-id="new-inside"'));
assert(!old.includes('data-station-id="new-north"'));
assert(wide.includes('data-station-id="new-inside"'));
assert(wide.includes('data-station-id="new-north"'));
assert(wide.includes('CMA-GD 全域'));
assert(!wide.includes('新疆站点单独评分'));
assert.deepEqual(base.cmagd_bounds,[93.64,16.6,126.34,42.88]);
for(const key of ['coastline','country_boundaries','province_boundaries']){
 assert(base[key+'_cmagd'].length);
 for(const line of base[key+'_cmagd'])for(const [x,y] of line)assert(x>=93.64&&x<=126.34&&y>=16.6&&y<=42.88);
}
const app=fs.readFileSync('app.js','utf8'),overview=fs.readFileSync('overview.js','utf8');
assert(app.includes("['all','cmagd','hn','hun','gba','sz','hk','xj']"));
assert(app.includes(".filter(m=>!cmagdScope()||cmagdModels.has(m))"));
for(const source of [app,overview])assert(source.includes('这个区域里新加的国家站只评 EC 0.25°、GFS、ECAI、GFSAI 四个模式。'));
assert(overview.includes('region:state.region'));
const windowFunction=app.match(/function stationWindow\(dataset=ds\)\{[\s\S]+?(?=\nfunction stationWindowNote)/)[0];
const ds={source:'station',region_nmic_majority:{hn:true,cmagd:true},window_end:'2026-11-16T06:00:00Z',
 nmic_end:'2026-11-15T09:00:00Z',cmagd_nmic_end:'2026-11-16T03:00:00Z'};
for(const [region,expected] of [['hn','2026-11-15'],['cmagd','2026-11-16']])
 assert.equal(vm.runInNewContext(windowFunction+'\nstationWindow()',{ds,stationRegion:region,Date,Math,Number}),expected);
console.log('通过：旧地图排除扩展站、全域地图显示南北扩展站、全域边界与四模式说明。');
