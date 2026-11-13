<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ArrowBackOutline, ArrowForwardOutline, ExpandOutline, ContractOutline, RefreshOutline, LocationOutline, SchoolOutline } from '@vicons/ionicons5'
import { fetchAtlas, type AtlasData, type AtlasSchool, type Counts } from '@/api/atlas'
import { fetchStatistics, type Statistics } from '@/api/statistics'
import { useAuthStore } from '@/stores/auth'
import AtlasTrend from './AtlasTrend.vue'

type Point = [number,number]
type Shape = {id:string;name:string;center:Point|null;rings:Point[][]}
type Row = Counts & {id:string;label:string;tag?:string}
const route = useRoute(), router = useRouter(), auth = useAuthStore()
const read=(key:string)=>typeof route.query[key]==='string' ? route.query[key] as string : ''
const department=computed(()=>read('department')), job=computed(()=>read('job'))
const region=computed(()=>read('region')), schoolId=computed(()=>read('school')), major=computed(()=>read('major'))
const tier=computed(()=>read('tier')), degree=computed(()=>read('degree'))
const period=computed(()=>read('period') || 'month')
const start=computed(()=>read('start')), end=computed(()=>read('end'))
const base = ref<Statistics>(), data = ref<AtlasData>()
const national=ref<Shape[]>([]), subdivision=ref<Shape[]>([])
const cityIndex=ref<Record<string,Record<string,Point>>>({})
const campusView=ref(false)
const campusAssets:Record<string,string>={'南京大学':'nanjing-university','安阳工学院':'anyang-institute'}
const areaFiles=new Map<string,Shape[]>()
const error=ref(''), busy=ref(true), isFullscreen=ref(false)
const screen=ref<HTMLElement>()
let seq=0, areasSeq=0
const metric=ref<'applications'|'hired'>('hired')
const hover=ref<(Counts & {name:string; sub?:string})>()
const zoom=ref(1)
const tiers=[['','全部院校'],['is_985','985'],['is_211','211'],['is_double_first_class','双一流'],['qs','QS 前 100']]
const degrees=[['','全部学历'],['bachelor','本科'],['master','硕士'],['phd','博士']]
const periods=[['month','本月'],['30','近 30 天'],['90','近 90 天'],['year','今年']]
const short=(name:string)=>name.replace(/维吾尔自治区|壮族自治区|回族自治区|自治区|特别行政区|省$|市$/g,'')
const names=computed(()=>Object.fromEntries(national.value.map(s=>[s.id,short(s.name)])))
const regionName=(id:string)=>id==='overseas'?'海外院校':id==='unknown'?'地区未核实':names.value[id] || id
const regionCount=computed(()=>new Map((data.value?.regions ?? []).map(r=>[r.id,r])))
const chosenSchool=computed(()=>data.value?.schools.find(s=>String(s.id)===schoolId.value))
const campusAsset=computed(()=>chosenSchool.value ? campusAssets[chosenSchool.value.school] : undefined)
watch(campusAsset,(asset)=>{campusView.value=!!asset})
const chosenDepartment=computed(()=>base.value?.departments.find(x=>String(x.id)===department.value)?.name)
const scopeTitle=computed(()=>chosenDepartment.value || (auth.isSuperAdmin?'全公司':'已授权部门'))
const locationTitle=computed(()=>chosenSchool.value?.school || (schoolId.value?'所选院校无匹配':region.value?regionName(region.value):'全国院校分布'))
const mapSelected=computed(()=>national.value.find(x=>x.id===region.value))
const tierColor:Record<string,string>={is_985:'#ba475b',is_211:'#a06724',is_double_first_class:'#6d55a5',qs:'#166f91',ordinary:'#467bc1'}
const tierName:Record<string,string>={is_985:'985',is_211:'211',is_double_first_class:'双一流',qs:'QS 前 100',ordinary:'其他院校'}
const schoolTier=(s:AtlasSchool)=>s.tier || (s.qs_rank?'qs':'ordinary')
const schoolLabel=(s:AtlasSchool)=>s.tier ? tierName[s.tier] : s.qs_rank ? 'QS #'+s.qs_rank : ''
const fmt=new Intl.NumberFormat('zh-CN')
const n=(v:number|undefined)=>fmt.format(v ?? 0)
const rows=computed<Row[]>(()=>{
  if (chosenSchool.value) return chosenSchool.value.majors.filter(r=>!major.value || r.major===major.value)
    .map(r=>({id:r.major,label:r.major,applications:r.applications,hired:r.hired}))
  if (schoolId.value) return []
  if (region.value) return (data.value?.schools ?? []).filter(s=>s.region===region.value)
    .map(s=>({id:String(s.id),label:s.school,tag:schoolLabel(s),applications:s.applications,hired:s.hired}))
  return (data.value?.regions ?? []).map(r=>({id:r.id,label:regionName(r.id),applications:r.applications,hired:r.hired}))
})
const maxRows=computed(()=>Math.max(1,...rows.value.map(r=>r[metric.value])))
const rankedRows=computed(()=>[...rows.value].sort((a,b)=>b[metric.value]-a[metric.value] || b.applications-a.applications))
const genderRows=computed(()=>[
  {key:'male',label:'男性',color:'#3476b7',value:data.value?.gender.male || 0},
  {key:'female',label:'女性',color:'#bd536d',value:data.value?.gender.female || 0},
  ...((data.value?.gender.other || 0)>0 ? [{key:'other',label:'其他',color:'#7762a7',value:data.value?.gender.other || 0}] : []),
])
function update(values:Record<string,string|undefined>) {
  hover.value=undefined
  return router.push({name:'dashboard',query:{...route.query,...values}})
}
function pickRegion(id:string) { void update({region:id,school:undefined,major:undefined}) }
function pickSchool(id:string, area?:string) { if (id!==schoolId.value || (area && region.value!==area)) void update({...(area ? {region:area} : {}),school:id,major:undefined}) }
function pickRow(row:Row) {
  if (chosenSchool.value) void update({major:row.id})
  else if (region.value) pickSchool(row.id)
  else pickRegion(row.id)
}
function up() {
  if (major.value) void update({major:undefined})
  else if (schoolId.value) void update({school:undefined,major:undefined})
  else void update({region:undefined,school:undefined,major:undefined})
}
function filter(event:Event,key:string) {
  const value=(event.target as HTMLSelectElement).value || undefined
  void update({[key]:value,...(key==='department'?{job:undefined}:{}),...(key==='period'?{start:undefined,end:undefined}:{})})
}
function countLabel(r:Counts) { return '投递 '+n(r.applications)+' 份 · 录取 '+n(r.hired)+' 人' }
function preview(row:Row) { hover.value={...row,name:row.label,sub:row.tag} }
const statsLink=computed(()=>({name:'statistics',query:{department:department.value || undefined,job:job.value || undefined}}))
function detailDay(day:string) {
  const p=data.value?.period
  if (!p) return
  const date=new Date(day+'T00:00:00Z')
  if (p.unit==='month') date.setUTCMonth(date.getUTCMonth()+1,0)
  if (p.unit==='week') date.setUTCDate(date.getUTCDate()+6)
  const dateEnd=date.toISOString().slice(0,10)
  void update({start:day < p.start?p.start:day,end:dateEnd > p.end?p.end:dateEnd})
}
function fsState() { isFullscreen.value=document.fullscreenElement===screen.value }
function full() {
  if (isFullscreen.value) void document.exitFullscreen().catch(()=>{})
  else void screen.value?.requestFullscreen().catch(()=>{error.value='当前窗口无法全屏。'})
}
async function readMap(key:string) {
  if (areaFiles.has(key)) return areaFiles.get(key)!
  const response=await fetch('/maps/'+key+'.json')
  if (!response.ok) throw new Error('地图未加载')
  const shapes=await response.json() as Shape[]
  if (!Array.isArray(shapes)) throw new Error('地图未加载')
  areaFiles.set(key,shapes)
  return shapes
}
async function load() {
  const id=++seq
  busy.value=true; error.value=''
  const params=new URLSearchParams({period:period.value})
  for (const [key,value] of [['department_id',department.value],['job_id',job.value],['region',region.value],
    ['school_id',schoolId.value],['start',start.value],['end',end.value],['major',major.value],['tier',tier.value],['degree',degree.value]]) if(value)params.set(key,value)
  try {
    const result=await fetchAtlas(params)
    if(id===seq) data.value=result
  } catch(e) {
    if(id===seq) {data.value=undefined;error.value=(e as Error).message}
  } finally { if(id===seq)busy.value=false }
}
watch(()=>[department.value,job.value,region.value,schoolId.value,major.value,tier.value,degree.value,period.value,start.value,end.value],load,{immediate:true})
watch(region, async key=>{
  const id=++areasSeq
  subdivision.value=[];zoom.value=1;hover.value=undefined
  if (/^\d{6}$/.test(key)) try { const shapes=await readMap(key); if(id===areasSeq)subdivision.value=shapes } catch { /* Province outline stays available. */ }
},{immediate:true})
watch(schoolId,()=>{zoom.value=1;hover.value=undefined})
onMounted(async()=>{
  document.addEventListener('fullscreenchange',fsState)
  const [map,meta,city]=await Promise.allSettled([readMap('china'),fetchStatistics(new URLSearchParams()),
    fetch('/maps/cities.json').then(r=>{if(!r.ok)throw new Error('城市位置未加载');return r.json()})])
  if(map.status==='fulfilled')national.value=map.value
  else error.value='底图暂时无法加载，可使用左侧表格查看地区。'
  if(meta.status==='fulfilled')base.value=meta.value
  if(city.status==='fulfilled')cityIndex.value=city.value
})
onBeforeUnmount(()=>{seq++;areasSeq++;document.removeEventListener('fullscreenchange',fsState)})
const activeCity=computed(()=>{
  if(!chosenSchool.value?.city) return undefined
  return subdivision.value.find(s=>s.name===chosenSchool.value?.city)
})
const shapes=computed(()=>!region.value ? national.value : activeCity.value ? [activeCity.value] :
  subdivision.value.length ? subdivision.value : mapSelected.value ? [mapSelected.value] : [])
const projection=computed(()=>{
  let x1=Infinity,x2=-Infinity,y1=Infinity,y2=-Infinity
  for(const s of shapes.value) for(const ring of s.rings)for(const [x,y] of ring) {
    x1=Math.min(x1,x);x2=Math.max(x2,x);y1=Math.min(y1,y);y2=Math.max(y2,y)
  }
  if(!Number.isFinite(x1)) return (_:Point):Point=>[320,230]
  const weight=!region.value?.length ? .86 : Math.cos((y1+y2)*Math.PI/360)
  const scale=Math.min(564/Math.max(0.02,(x2-x1)*weight),394/Math.max(.02,y2-y1))
  return ([x,y]:Point):Point=>[320+(x-(x1+x2)/2)*scale*weight,221+(y2+y1-2*y)/2*scale]
})
const pathRows=computed(()=>shapes.value.map(s=>({...s,d:s.rings.map(r=>r.map((point,i)=>{
  const [x,y]=projection.value(point);return (i?'L':'M')+x.toFixed(1)+' '+y.toFixed(1)
}).join('')+'Z').join('')})))
const maxRegion=computed(()=>Math.max(1,...(data.value?.regions ?? []).map(r=>r[metric.value])))
function fillFor(id:string) {
  if(region.value) return '#dcecf6'
  const count=regionCount.value.get(id)?.[metric.value] ?? 0
  if(!count)return '#e6eef6'
  return 'hsl(210 60% '+(80-count/maxRegion.value*19)+'%)'
}
const pins=computed(()=>{
  if(!shapes.value.length || (schoolId.value && !chosenSchool.value))return []
  const schools=chosenSchool.value?[chosenSchool.value]:(data.value?.schools ?? []).filter(s=>
    /^\d{6}$/.test(s.region) && (!region.value || s.region===region.value) && (s.applications || s.hired))
  const grouped=new Map<string,number>()
  return schools.map(s=>{
    const place=s.region+':'+(s.city ?? '')
    const same=grouped.get(place) ?? 0
    grouped.set(place,same+1)
    const p=(s.city ? cityIndex.value[s.region]?.[s.city] : null) || national.value.find(g=>g.id===s.region)?.center
    const [x,y]=p ? projection.value(p):[320,221]
    const distance=region.value ? 9 : 5
    return {s,x:x+(same?Math.cos(same*2.4)*distance*Math.sqrt(same):0),
      y:y+(same?Math.sin(same*2.4)*distance*Math.sqrt(same):0)}
  })
})
</script>

<template>
  <main ref="screen" class="atlas-screen" :aria-busy="busy">
    <div class="atlas-shell">
      <header class="atlas-header">
        <div><div class="scope-line"><i></i>{{ auth.isSuperAdmin ? '公司招聘总览' : '部门招聘工作台' }} <span>{{ scopeTitle }}</span></div>
          <h1>招聘人才地图</h1><p>按院校所在地看投递与录取，逐级查看地区、院校和专业。</p></div>
        <div class="atlas-actions">
          <RouterLink :to="statsLink" class="rs-button primary">统计管理 <ArrowForwardOutline aria-hidden="true"/></RouterLink>
          <button type="button" class="rs-button icon-button" aria-label="刷新大屏" title="刷新大屏" @click="load"><RefreshOutline aria-hidden="true"/></button>
          <button type="button" class="rs-button icon-button" :aria-label="isFullscreen?'退出全屏':'全屏大屏'" @click="full">
            <ContractOutline v-if="isFullscreen" aria-hidden="true"/><ExpandOutline v-else aria-hidden="true"/>
          </button>
        </div>
      </header>

      <div class="atlas-filters" aria-label="大屏筛选">
        <label>时间<select :value="period" @change="filter($event,'period')"><option v-for="[id,label] in periods" :key="id" :value="id">{{ label }}</option></select></label>
        <label v-if="data?.school_tags_allowed">院校标签<select :value="tier" @change="filter($event,'tier')"><option v-for="[id,label] in tiers" :key="id" :value="id">{{ label }}</option></select></label>
        <label v-if="data?.degree_allowed">学历<select :value="degree" @change="filter($event,'degree')"><option v-for="[id,label] in degrees" :key="id" :value="id">{{ label }}</option></select></label>
        <label>部门<select :value="department" @change="filter($event,'department')"><option value="">{{ auth.isSuperAdmin?'全部部门':'全部授权部门' }}</option><option v-for="d in base?.departments" :key="d.id" :value="d.id">{{ d.name }}</option></select></label>
        <label>岗位<select :value="job" @change="filter($event,'job')"><option value="">全部岗位</option><option v-for="j in base?.job_options.filter(j=>!department || String(j.department_id)===department)" :key="j.id" :value="j.id">{{ j.title }}</option></select></label>
        <button v-if="start && end" type="button" class="rs-button" @click="update({start:undefined,end:undefined})">{{ start }} 至 {{ end }} ×</button>
      </div>

      <p v-if="error" class="rs-error" role="alert">{{ error }} <button class="rs-button" @click="load">重试</button></p>
      <div v-if="!data && busy" class="atlas-loading" role="status">正在汇总招聘分布…</div>
      <div v-if="data" class="atlas-grid">
        <aside class="atlas-stack">
          <section class="atlas-panel atlas-kpis">
            <h2>招聘概况</h2>
            <div class="metric-pair">
              <button type="button" :aria-pressed="metric==='applications'" @click="metric='applications'" :title="countLabel(data)">
                <span>投递简历</span><strong>{{ n(data.applications) }}<small> 份</small></strong>
              </button>
              <button type="button" :aria-pressed="metric==='hired'" @click="metric='hired'" :title="countLabel(data)">
                <span>录取人数</span><strong>{{ n(data.hired) }}<small> 人</small></strong>
              </button>
            </div>
            <p class="panel-note">{{ data.period.start }} 至 {{ data.period.end }}</p>
          </section>
          <section class="atlas-panel">
            <h2>{{ schoolId ? '专业排名' : region ? '院校排名' : '地区排名' }}</h2>
            <p class="panel-note">{{ metric==='hired'?'按录取排序':'按投递排序' }}，点击查看下一层</p>
            <div v-if="rankedRows.length" class="rank-bars">
              <button v-for="row in rankedRows.slice(0,5)" :key="row.id" type="button" :title="countLabel(row)"
                @mouseenter="preview(row)" @focus="preview(row)" @mouseleave="hover=undefined" @blur="hover=undefined" @click="pickRow(row)">
                <span>{{ row.label }}</span><strong>{{ n(row[metric]) }}</strong>
                <span class="rank-track"><i :style="{width:row[metric]/maxRows*100+'%'}"></i></span>
              </button>
            </div>
            <p v-else class="panel-empty">当前条件下没有可归类的数据。</p>
          </section>
          <section class="atlas-panel">
            <h2>{{ schoolId?'专业明细':region?'院校明细':'地区明细' }}</h2>
            <div class="atlas-table-wrap">
              <table class="atlas-table">
                <thead><tr><th>{{ schoolId?'专业':region?'院校':'地区' }}</th><th>投递</th><th>录取</th></tr></thead>
                <tbody><tr v-for="row in rankedRows" :key="row.id">
                  <td><button type="button" :title="countLabel(row)" @click="pickRow(row)" @mouseenter="preview(row)" @focus="preview(row)" @mouseleave="hover=undefined" @blur="hover=undefined">{{ row.label }}</button>
                    <small v-if="row.tag">{{ row.tag }}</small></td>
                  <td>{{ n(row.applications) }}</td><td>{{ n(row.hired) }}</td>
                </tr></tbody>
              </table>
            </div>
            <p v-if="!rankedRows.length" class="panel-empty">暂无明细。</p>
          </section>
        </aside>

        <section class="atlas-panel map-panel" aria-label="交互式招聘地图">
          <header class="map-heading">
            <div><div class="map-path"><button v-if="region" type="button" @click="up"><ArrowBackOutline aria-hidden="true"/>返回上一级</button><span v-else>地域分布</span></div>
              <h2>{{ locationTitle }}<small v-if="major"> / {{ major }}</small></h2></div>
            <div class="map-switch" aria-label="地图着色">
              <button type="button" :aria-pressed="metric==='applications'" @click="metric='applications'">投递</button>
              <button type="button" :aria-pressed="metric==='hired'" @click="metric='hired'">录取</button>
            </div>
          </header>
          <div class="map-frame">
            <div class="map-zoom" v-if="shapes.length && !campusView">
              <button type="button" aria-label="放大地图" :disabled="zoom>=2" @click="zoom=Math.min(2,zoom+.2)">+</button>
              <button type="button" aria-label="缩小地图" :disabled="zoom<=1" @click="zoom=Math.max(1,zoom-.2)">−</button>
              <button type="button" aria-label="重置缩放" @click="zoom=1">↺</button>
            </div>
            <img v-if="campusView && campusAsset" class="campus-frame campus-image" :src="'/maps/campus/'+campusAsset+'.webp'"
              :alt="chosenSchool?.school+' 官方校园地图'">
            <iframe v-else-if="campusView && chosenSchool?.campus_map_url && !chosenSchool.campus_map_url.endsWith('.jpg')"
              :src="chosenSchool.campus_map_url" class="campus-frame" :title="chosenSchool.school+' 官方校园地图'" referrerpolicy="no-referrer"
              sandbox="allow-scripts allow-same-origin" />
            <img v-else-if="campusView && chosenSchool?.campus_map_url" class="campus-frame campus-image" :src="chosenSchool.campus_map_url"
              :alt="chosenSchool.school+' 官方校园地图'" referrerpolicy="no-referrer">
            <svg v-else-if="shapes.length" class="geo-map" viewBox="0 0 640 450" aria-label="按地区选择招聘数据">
              <g :transform="'translate(320 222) scale('+zoom+') translate(-320 -222)'">
                <g v-for="shape in pathRows" :key="shape.id">
                  <path :d="shape.d" :fill="fillFor(shape.id)" fill-rule="evenodd" :class="{ 'click-area':!region && shape.name }"
                    :tabindex="!region && shape.name?0:undefined" :role="!region && shape.name?'button':undefined"
                    :aria-label="!region && shape.name ? shape.name+'，'+countLabel(regionCount.get(shape.id) || {applications:0,hired:0}) : undefined"
                    @click="!region && shape.name && pickRegion(shape.id)" @keydown.enter="!region && shape.name && pickRegion(shape.id)"
                    @keydown.space.prevent="!region && shape.name && pickRegion(shape.id)"
                    @mouseenter="!region && shape.name && (hover={name:shape.name,...(regionCount.get(shape.id) || {applications:0,hired:0})})"
                    @focus="!region && shape.name && (hover={name:shape.name,...(regionCount.get(shape.id) || {applications:0,hired:0})})"
                    @mouseleave="hover=undefined" @blur="hover=undefined" />
                  <text v-if="!region && shape.name && shape.center && regionCount.has(shape.id)" :x="projection(shape.center)[0]" :y="projection(shape.center)[1]+4" class="region-text">{{ short(shape.name) }}</text>
                </g>
                <g v-for="pin in pins" :key="pin.s.id" :transform="'translate('+pin.x+' '+pin.y+')'" class="school-pin" :class="{ 'national-pin':!region }"
                  :role="region && !chosenSchool ? 'button' : undefined" :tabindex="region && !chosenSchool ? 0 : undefined" :aria-label="pin.s.school+'，'+countLabel(chosenSchool ? data : pin.s)"
                  @click="region && pickSchool(String(pin.s.id))" @keydown.enter="region && pickSchool(String(pin.s.id))" @keydown.space.prevent="region && pickSchool(String(pin.s.id))"
                  @mouseenter="hover={...(chosenSchool ? data : pin.s),name:pin.s.school,sub:schoolLabel(pin.s)}"
                  @focus="hover={...(chosenSchool ? data : pin.s),name:pin.s.school,sub:schoolLabel(pin.s)}"
                  @mouseleave="hover=undefined" @blur="hover=undefined">
                  <circle :r="region ? 14 : 8" :fill="tierColor[schoolTier(pin.s)]" opacity=".22"/>
                  <circle :r="region ? 7 : 4" :fill="tierColor[schoolTier(pin.s)]" stroke="white" stroke-width="1.5"/>
                  <text v-if="region" x="10" y="-7">{{ chosenSchool ? '院校所在城市' : pin.s.school }}</text>
                </g>
              </g>
            </svg>
            <div v-else class="map-empty">
              <LocationOutline aria-hidden="true"/><strong>{{ region==='overseas'?'海外院校':region==='unknown'?'院校所在地尚未核实':'正在准备地图' }}</strong>
              <p>{{ region==='overseas'?'按学校所在国家或地区查看列表。':'可使用左侧数据表查看可用明细。' }}</p>
              <button v-for="s in (data.schools.filter(x=>x.region===region && (!schoolId || String(x.id)===schoolId))).slice(0,10)" :key="s.id"
                type="button" @click="pickSchool(String(s.id))">{{ s.school }}<small>{{ s.location }} · {{ countLabel(chosenSchool ? data : s) }}</small></button>
            </div>
            <div v-if="hover" class="map-tooltip" role="status"><strong>{{ hover.name }} <small>{{ hover.sub }}</small></strong><span>{{ countLabel(hover) }}</span></div>
          </div>
          <div v-if="!region" class="map-special">
            <button v-for="key in ['overseas','unknown']" :key="key" type="button" @click="pickRegion(key)">{{ regionName(key) }} <strong>{{ n(regionCount.get(key)?.[metric]) }}</strong></button>
          </div>
          <div class="map-legend"><span v-for="(label,key) in tierName" :key="key"><i :style="{background:tierColor[key]}"></i>{{ label }}</span></div>
          <div v-if="chosenSchool" class="campus-meta">
            <SchoolOutline aria-hidden="true"/><span>{{ schoolLabel(chosenSchool) || '院校' }} · {{ chosenSchool.city || chosenSchool.location }} · {{ countLabel(data) }}</span>
            <button v-if="chosenSchool.campus_map_url" type="button" class="rs-button" @click="campusView=!campusView">{{ campusView ? '返回城市地图' : '查看校园地图' }}</button>
            <a v-if="campusView && chosenSchool.campus_map_url" :href="chosenSchool.campus_map_url" target="_blank" rel="noopener noreferrer">在新窗口打开</a>
          </div>
          <p class="map-note">地区以已核实院校所在地计。院校标记采用城市近似位置，不代表校区坐标。</p>
        </section>

        <aside class="atlas-stack">
          <section class="atlas-panel">
            <h2>{{ period==='month' && !start?'本月投递与录取':'投递与录取趋势' }}</h2>
            <AtlasTrend :key="[region,schoolId,major,degree,tier,period].join('|')" :rows="data.trend" :unit="data.period.unit" @drill="detailDay"/>
          </section>
          <section class="atlas-panel">
            <h2>当前范围明细</h2>
            <table class="atlas-table scope-table"><tbody>
              <tr><th>统计范围</th><td>{{ locationTitle }}</td></tr>
              <tr v-if="major"><th>专业</th><td>{{ major }}</td></tr>
              <tr><th>投递简历</th><td>{{ n(data.applications) }} 份</td></tr>
              <tr><th>录取</th><td>{{ n(data.hired) }} 人</td></tr>
              <tr v-if="chosenSchool"><th>院校标签</th><td>{{ schoolLabel(chosenSchool) || '—' }}</td></tr>
            </tbody></table>
          </section>
          <section class="atlas-panel gender-panel">
            <h2>录取人员性别分布</h2>
            <p class="panel-note">图标表示占录取总人数的比例</p>
            <template v-if="data.gender_allowed">
              <div v-for="row in genderRows" :key="row.key" class="pictogram-row" :style="{color:row.color}" :title="row.label+' '+row.value+' 人'">
                <span>{{ row.label }}</span><span class="person-icons" aria-hidden="true">
                  <svg v-for="i in 10" :key="i" viewBox="0 0 12 22" :style="{opacity:i<=Math.round(row.value/Math.max(1,data.hired)*10)?1:.2}">
                    <circle cx="6" cy="3.2" r="2.3" fill="currentColor"/>
                    <path :d="row.key==='female'?'M3.5 6.2h5L11 15H8v5.6H6.5V15h-1v5.6H4V15H1z':'M3 6h6l1.3 7.3H8.5L8 10v10.7H6.6v-6H5.4v6H4V10l-.6 3.3H1.7z'" fill="currentColor"/>
                  </svg>
                </span><strong>{{ n(row.value) }}</strong>
              </div>
              <p class="panel-note">未披露 {{ n(data.gender.unknown) }} 人</p>
            </template>
            <p v-else class="panel-empty">当前未启用性别字段的展示权限。</p>
          </section>
        </aside>
      </div>
      <footer class="atlas-footer">
        <span>投递按申请计，录取按范围内首次录取计。时间按北京时间。</span>
        <nav aria-label="业务入口">
          <RouterLink to="/jobs">岗位管理</RouterLink><RouterLink :to="statsLink">统计管理</RouterLink>
          <RouterLink v-if="!auth.isSuperAdmin" to="/applications">简历管理</RouterLink><RouterLink v-else to="/admin">系统设置</RouterLink>
        </nav>
      </footer>
    </div>
  </main>
</template>

<style scoped>
.atlas-screen { --ink:#183652; --muted:#496580; --line:#ccdfed; --blue:#2868a9; --sky:#f2f8fc; height:100%; overflow:auto; color:var(--ink);
  background:radial-gradient(ellipse at 50% 28%,#e4f3fb 0,transparent 60%),#eff5fa; }
.atlas-shell { max-width:1840px; margin:auto; padding:clamp(12px,1.8vw,26px); display:grid; gap:13px; }
.atlas-header { display:flex; align-items:flex-end; justify-content:space-between; gap:12px; padding:4px 0 2px; }
.atlas-header h1 { font-size:clamp(24px,2.4vw,39px); margin:3px 0 4px; letter-spacing:-.04em; }
.atlas-header p,.panel-note,.map-note,.atlas-footer { font-size:12px; color:var(--muted); }
.scope-line { font-size:12px; font-weight:750; display:flex; align-items:center; gap:7px; }
.scope-line i,.map-legend i { width:8px; height:8px; border-radius:50%; background:#268681; flex:none; }
.scope-line span { font-weight:500; color:var(--muted); border-left:1px solid var(--line); padding-left:8px; }
.atlas-actions,.atlas-filters,.atlas-footer nav { display:flex; align-items:center; flex-wrap:wrap; gap:8px; }
.atlas-actions .rs-button { display:inline-flex; align-items:center; justify-content:center; gap:5px; text-decoration:none; min-height:40px; }
.atlas-actions svg,.map-path svg,.campus-meta svg { width:16px;height:16px;flex:none; }
.icon-button { width:40px; }
.atlas-filters { padding:8px 12px; border:1px solid var(--line); border-radius:8px; background:#e7f0f8; gap:8px 15px; }
.atlas-filters label { display:flex; align-items:center; gap:6px; font-size:12px; }
.atlas-filters select { max-width:190px; min-height:35px; padding:4px 7px; border:1px solid #c5d8e8; border-radius:5px; background:white; color:var(--ink); }
.atlas-grid { display:grid; grid-template-columns:minmax(210px,.82fr) minmax(0,1.95fr) minmax(228px,.94fr); gap:11px; align-items:start; }
.atlas-stack { display:grid; gap:10px; min-width:0; }
.atlas-panel { min-width:0; background:#fcfeff; border:1px solid var(--line); border-radius:9px; padding:11px 12px; position:relative; }
.atlas-panel::before { content:''; position:absolute; width:28px;height:2px; left:12px;top:-1px;background:#528dba; }
.atlas-panel h2 { font-size:15px; line-height:1.38; margin:0 0 4px; }
.metric-pair { display:grid; grid-template-columns:1fr 1fr; gap:6px; margin:9px 0 6px; }
.metric-pair button,.map-switch button,.map-special button,.map-zoom button,.map-empty button { border:1px solid var(--line); border-radius:6px; background:#edf5fb; color:var(--ink); cursor:pointer; padding:5px 7px; }
.metric-pair button { display:grid; text-align:left; padding:6px 7px; }
.metric-pair button[aria-pressed=true],.map-switch button[aria-pressed=true] { border-color:#8bb4d5;background:#e3f0fb; color:#164f83; }
.metric-pair span,.metric-pair small { font-size:11px; color:var(--muted); }
.metric-pair strong { font-size:clamp(19px,2vw,28px); font-variant-numeric:tabular-nums; }
.rank-bars { display:grid; gap:5px; margin-top:8px; }
.rank-bars button { display:grid; grid-template-columns:minmax(0,1fr) auto; gap:4px; padding:4px 2px; border:0; background:none; color:var(--ink); text-align:left; cursor:pointer; font-size:12px; }
.rank-bars button>span:first-child { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.rank-track { grid-column:1/-1; height:4px; background:#e7eff6; border-radius:2px; }
.rank-track i { display:block;height:100%;border-radius:2px;background:#4a89be; }
.atlas-table-wrap { max-height:240px; overflow:auto; margin-top:7px; }
.atlas-table { width:100%; border-collapse:collapse; font-size:12px; text-align:left; }
.atlas-table th,.atlas-table td { padding:6px 4px; border-bottom:1px solid #e2ebf4; vertical-align:middle; }
.atlas-table thead th { background:#edf4fa; position:sticky; top:0; z-index:1; white-space:nowrap; }
.atlas-table td:not(:first-child) { white-space:nowrap; font-variant-numeric:tabular-nums; }
.atlas-table td button,.map-path button { cursor:pointer; background:none; border:0; padding:3px 0; color:#225a8b; text-align:left; }
.atlas-table td button:hover,.map-path button:hover { text-decoration:underline; }
.atlas-table td small { display:block; font-size:10px; color:#5c4c87; }
.map-panel { padding:12px; }
.map-heading { display:flex; justify-content:space-between; align-items:flex-end; gap:8px; margin-bottom:8px; }
.map-heading h2 { margin:3px 0 0; font-size:clamp(17px,1.75vw,25px); }
.map-heading h2 small { font-size:13px; }
.map-path { color:var(--muted); font-size:12px; }
.map-path button { display:inline-flex; gap:4px; align-items:center; }
.map-switch { display:flex; gap:4px; flex:none; }
.map-switch button { font-size:12px; }
.map-frame { position:relative; border:1px solid #d2e5f4; border-radius:7px; overflow:hidden; display:grid; place-items:center;
  min-height:clamp(320px,39vw,620px); background-color:#f1f8fd; background-image:linear-gradient(#87a9c61c 1px,transparent 1px),linear-gradient(90deg,#87a9c61c 1px,transparent 1px); background-size:23px 23px; }
.geo-map { display:block; width:100%; max-height:610px; }
.geo-map path { stroke:#f7fcff; stroke-width:1; vector-effect:non-scaling-stroke; }
.geo-map path.click-area,.school-pin { cursor:pointer; }
.geo-map path.click-area:hover,.geo-map path.click-area:focus-visible { stroke:#2e69a4; stroke-width:2; outline:none; }
.national-pin { pointer-events:none; }
.campus-frame { width:100%; height:clamp(310px,38vw,610px); border:0; }
.campus-image { object-fit:contain; }
.geo-map text { pointer-events:none; font-size:11px; fill:#1d4f76; font-weight:700; paint-order:stroke; stroke:#f8fcff;stroke-width:3; }
.geo-map .region-text { text-anchor:middle; }
.school-pin:hover circle:last-of-type,.school-pin:focus-visible circle:last-of-type { stroke:#183652; stroke-width:2; }
.map-zoom { position:absolute; z-index:2; right:8px; top:8px; display:grid; gap:3px; }
.map-zoom button { width:31px; height:31px; padding:0; font-size:16px; background:#fff; }
.map-zoom button:disabled { opacity:.45; cursor:default; }
.map-tooltip { position:absolute; z-index:2; bottom:8px; left:8px; pointer-events:none; display:grid; gap:3px; padding:8px 11px; background:#204361; color:#fff; border-radius:5px; font-size:12px; max-width:80%; }
.map-tooltip small { color:#deebfa; }
.map-legend,.map-special,.campus-meta { display:flex; gap:7px 10px; flex-wrap:wrap; align-items:center; margin-top:8px; font-size:11px; }
.map-legend span { display:inline-flex; align-items:center; gap:4px; color:var(--muted); }
.map-note { margin:8px 0 0; }
.map-empty { display:grid; justify-items:center; gap:5px; text-align:center; padding:15px; font-size:13px; }
.map-empty svg { width:30px; color:#467cab; }
.map-empty button small { display:block; color:var(--muted); }
.campus-meta a,.atlas-footer a { color:#225a8b; text-underline-offset:3px; }
.panel-empty { padding:9px 0 3px; color:var(--muted); font-size:12px; }
.pictogram-row { display:grid; grid-template-columns:32px minmax(0,1fr) auto; gap:6px; align-items:center; margin-top:7px; font-size:12px; }
.pictogram-row strong { font-variant-numeric:tabular-nums; }
.person-icons { display:grid; grid-template-columns:repeat(10,minmax(0,1fr)); gap:2px; }
.person-icons svg { width:100%; height:18px; }
.gender-panel .panel-note:last-child { margin-top:7px; }
.atlas-footer { display:flex; justify-content:space-between; flex-wrap:wrap; gap:8px; }
.atlas-loading { min-height:360px; display:grid; place-items:center; color:var(--muted); }
.atlas-screen :is(a,button,select):focus-visible { outline:2px solid #2d6ca8; outline-offset:2px; }
@media(max-width:1180px) { .atlas-grid { grid-template-columns:minmax(180px,.8fr) minmax(0,1.9fr); }.atlas-grid>.atlas-stack:last-child { grid-column:1/-1; grid-template-columns:repeat(3,minmax(0,1fr)); } }
@media(max-width:760px) { .atlas-header { align-items:flex-start; flex-direction:column; }.atlas-grid { display:flex; flex-direction:column; }.atlas-grid>* { width:100%; }.map-panel { order:-1; }.atlas-grid>.atlas-stack:last-child { grid-template-columns:1fr; }.atlas-filters select { max-width:135px; }.map-frame { min-height:280px; } }
</style>
