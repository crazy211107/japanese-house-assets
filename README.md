# 资产流水线（云端 Blender → GLB）

这个目录就是那个 GitHub 仓库的全部内容。放上去之后，每次改动 bpy 脚本，
GitHub Actions 会用 **Blender 4.2 LTS** 在云端建模、倒角、上顶点色、导出 GLB，
再把 `dist/stone-lantern.glb` 和构建日志自动提交回仓库。

你的电脑不需要装 Blender，也不参与计算。

---

## 为什么不是我直接建仓库

我用 GitHub 连接器尝试创建仓库时被拒了：

```
POST /user/repos  -> 403 Resource not accessible by integration
```

当前连接器是一个 GitHub App 安装令牌，没有「创建仓库」的权限，`gh` CLI 本机也没装。
所以建仓库这一步需要你点一下（大约 1 分钟），之后推文件我可以再试一次连接器。

---

## 三步跑通

### 1. 建仓库

GitHub 右上角 **+ → New repository**

| 字段 | 值 |
| --- | --- |
| Repository name | `japanese-house-assets` |
| 可见性 | Private（推荐） |
| 初始化 | 勾 **Add a README file** |

### 2. 上传两个文件

网页里 **Add file → Create new file**，路径直接连斜杠一起输入：

| 路径 | 内容来自 |
| --- | --- |
| `tools/build_lantern.py` | 本目录 `tools/build_lantern.py` |
| `.github/workflows/build-glb.yml` | 本目录 `.github/workflows/build-glb.yml` |

粘贴内容 → Commit changes。第 2 个文件提交后，Actions 会自动开始跑
（分支保护或首次启用可能需要你在 Actions 页点一次 "I understand my workflows"）。

### 3. 等结果

约 2–4 分钟。跑完后仓库里会多出：

```
dist/
├── stone-lantern.glb     ~100–400 KB
└── build.log             面数、尺寸、导出参数
```

如果失败，`dist/build.log` 里会有完整的 Blender 报错 —— 这是刻意设计的：
Actions 的运行日志在仓库外拿不到，所以工作流用 `if: always()` 把日志也提交回来。

---

## 本地怎么用这个 GLB

把 `stone-lantern.glb` 下载到 `3d-japanese-house/assets/models/`，然后在
Three.js 里替换现有的石灯笼：

```js
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';

const draco = new DRACOLoader().setDecoderPath(
  'https://unpkg.com/three@0.160.0/examples/jsm/libs/draco/'
);
const loader = new GLTFLoader().setDRACOLoader(draco);

loader.load('./assets/models/stone-lantern.glb', (gltf) => {
  const lantern = gltf.scene;
  lantern.position.set(6.4, 0, 9.6);   // 与现有石灯笼同位
  lantern.traverse((o) => {
    if (o.isMesh) {
      o.castShadow = true;
      o.receiveShadow = true;
      o.material.envMapIntensity = 1.15;   // 与本项目其它石材一致
    }
  });
  world.scene.add(lantern);
});
```

注意两点：

1. 模型已经在 Blender 里贴地（底面 z=0），导入后不要再调 y；
2. 顶点色（COLOR_0）会被自动乘到 baseColor 上，**不要**把
   `material.vertexColors` 手动关掉，否则风化与苔痕会消失。

---

## 手动推送（如果你更习惯命令行）

```bash
cd asset-pipeline
git init -b main
git add .
git commit -m "feat: stone lantern pipeline"
git remote add origin https://github.com/<你的账号>/japanese-house-assets.git
git push -u origin main
```

---

## 这一版做了什么

| 部件 | 做法 |
| --- | --- |
| 基础（二重方石） | 立方体 + 2% 收分 + 倒角 |
| 竿 | 八棱锥台，上细下粗（0.105 → 0.083） |
| 竿受 / 中台 | 圆盘 + 方台，托住火袋 |
| 火袋 | 底板 + 四角柱 + 上下压条，中间真正镂空给光 |
| 笠 | 四坡锥体，檐口外沿抬 3cm 做极简起翘 |
| 宝珠 | UV 球 + 颈座 |
| 风化 | 顶点色：石面明暗 + 底部积灰 + 朝上面长苔 |

面数预算 ≤ 12,000 三角面，脚本里直接 `assert`，超了就构建失败 —— 不让"优化"
变成一句口头承诺。

---

## 下一步

跑通石灯笼之后，同一套结构可以直接扩到：

1. `tools/build_house_shell.py`（柱梁檐口椽列）
2. `tools/build_roof.py`（实体瓦垄 + 鬼瓦）
3. `tools/build_shoji.py`（真实木框截面 + 榫）

每个脚本一个 GLB，工作流里循环调用即可。
