# PDF2EPUB 使用手册 | [English](README_en.md)

PDF2EPUB 是一个强大的工具，可以将 PDF 书籍转换为 EPUB 格式，并可以将 EPUB 书籍从一种语言翻译成另一种语言。该工具利用 Google Gemini API 进行 PDF 解析和翻译，并使用 S3 兼容存储来保存和同步文件。

## [推荐] 使用 GitHub Actions 运行

**由于中国大陆无法直接访问 Google Gemini API，强烈建议使用 GitHub Actions 进行自动化处理。**

1. Fork 本仓库到你的 GitHub 账户

2. 转到分叉仓库的 `Setting` 选项卡

3. 转到 `Secrets and variables - Actions` 选项卡

4. 添加一个名为 `CONFIG` 的新密钥，内容为你的完整 `config.yaml` 文件内容（参考下方配置说明）

5. 在 GitHub 仓库页面，转到 Actions 选项卡

6. 选择要运行的工作流：
   - `Convert pdf to epub` - 将 PDF 转换为 EPUB
   - `Translate EPUB` - 翻译 EPUB 文件

7. 点击 "Run workflow" 按钮启动处理

8. 处理完成后，结果将保存在你配置的 S3 存储中

### 设置 S3 兼容存储

#### 选项 1：Backblaze B2（推荐）

如果没有美国信用卡，可以在 [Backblaze](https://www.backblaze.com/) 获取免费的 10GB 存储空间：

1. 注册 Backblaze 账户
2. 在 [我的设置](https://secure.backblaze.com/account_settings.htm) 里面启用 B2 云存储
3. 在 [应用密钥](https://secure.backblaze.com/app_keys.htm) 页面生成新的主应用程序密钥（可以记录 keyID 和 applicationKey，但不会用到）
4. 创建一个 S3 存储桶，例如命名为 `translator`
5. 记录下 Endpoint，前面加上 `https://` 后就是之后要用到的 `s3_endpoint`
6. 添加新的应用程序密钥，选择允许访问所有（all）存储桶
7. 记录下 keyID（对应配置中的 `s3_access_key_id`）和 applicationKey（对应配置中的 `s3_secret_access_key`）

#### 选项 2：Cloudflare R2

如果有美国信用卡，可以在 [Cloudflare](https://developers.cloudflare.com/r2/) 获取免费的 10GB 存储空间：

1. 登录 Cloudflare 账户
2. 转到 R2 - Manage R2 API Tokens - Create API Token
3. 允许读写权限
4. 记下 access key、secret key 和 endpoint（完整的 URL，包括 https://）
5. 创建一个 S3 存储桶，例如命名为 `book`

### 获取 Google API 密钥

1. 访问 [Google AI Studio](https://makersuite.google.com/app/apikey)
2. 创建一个 API 密钥
3. 将密钥复制到配置文件中的 `google_api_key` 字段

### 配置文件说明

创建一个 `config.yaml` 文件，填入以下信息：

```yaml
title: 书籍原始标题
target_title: 书籍翻译后的标题
author: 作者名
google_api_key: 你的Google API密钥
model: gemini-2.5-pro-preview-03-25
target_language: Chinese
source_language: English
s3_access_key_id: 你的S3访问密钥ID
s3_secret_access_key: 你的S3访问密钥
s3_bucket_name: 你的S3存储桶名称
s3_endpoint: 你的S3端点URL
num_retries: 3  # API调用失败时的重试次数
max_backoff_seconds: 30  # 重试时的最大退避时间（秒）
previous_content_limit: 0  # 设置翻译时使用的前文上下文字符数（0表示不使用上下文，可减少Token消耗）
```

## 本地运行

如果你能够直接访问 Google Gemini API，也可以在本地运行：

### 系统要求

- Python 3.11+
- Poetry（依赖管理）
- Google Gemini API 密钥
- S3 兼容存储（可选，用于文件同步和备份）

### 安装步骤

1. 克隆仓库：

```bash
git clone https://github.com/yourusername/pdf2epub.git
cd pdf2epub
```

2. 使用 Poetry 安装依赖：

```bash
pip install poetry
poetry install
```

3. 复制示例配置文件：

```bash
cp config.yaml.example config.yaml
```

4. 编辑 `config.yaml` 文件，填入必要信息

### 使用方法

1. 将 PDF 文件放在 `output/书名/input.pdf` 路径下

2. 运行 PDF 结构分析：

```bash
python src/breakdown.py -c config.yaml -i output/书名/input.pdf
```

3. 生成 EPUB：

```bash
python src/generate_epub.py --input output/书名/input.pdf --config config.yaml
```

4. 翻译 EPUB（可选）：

```bash
python src/translate_epub.py --input output/书名/input.epub --config config.yaml
```

## 功能特点

- 将 PDF 书籍转换为 EPUB 格式
- 翻译 EPUB 书籍（支持多种语言）
- 自动提取和处理书籍结构（目录、章节等）
- 保留原始格式和图片
- 支持大型 PDF 文件的压缩
- 使用 S3 兼容存储进行文件同步和备份
- 支持 GitHub Actions 自动化处理

## 文件路径格式

处理后的文件将按以下结构组织：

```
output/
└── 书名/
    ├── input.pdf             # 原始PDF文件
    ├── book_structure.json   # 书籍结构数据
    ├── 书名.epub             # 生成的EPUB文件
    ├── 翻译后书名.epub       # 翻译后的EPUB文件（如果进行了翻译）
    ├── generation_progress.json  # EPUB生成进度
    ├── translation_progress.json # 翻译进度
    ├── extract/              # EPUB提取的内容
    ├── translated/           # 翻译后的内容
    └── epub/                 # EPUB构建文件
```

## 配置说明

根据你的需求，某些配置项可能是可选的：

- 如果只进行 PDF 到 EPUB 的转换（不翻译）：
  - 不需要设置 `target_title`、`target_language`、`source_language`
  
- 如果只进行 EPUB 翻译（不从 PDF 生成 EPUB）：
  - 所有配置项都需要设置

## 注意事项

- 目前只推荐使用 gemini-pro-2.5 模型，因为只有这个模型能将 PDF 直接转换为 HTML
- 书名中不要加特殊符号，以避免文件路径问题
- PDF 文件大小限制为 45MB，超过此限制的文件将被自动压缩
- 处理大型 PDF 可能需要较长时间，请耐心等待
- 翻译过程可能会因为 API 限制而暂时失败，系统会自动重试
- **目前 EPUB 生成功能主要在日文书籍上进行了测试**，其他语言可能需要调整
- **推荐使用 Calibre 进行格式转换**：由于 Gemini 生成的 HTML 可能不完全符合标准，建议在输入前和输出后使用 Calibre 进行格式转换，以确保最佳兼容性

## 贡献

欢迎提交 Pull Request 或创建 Issue 来改进这个项目。

## 支持开发者

不用打钱也不用点星星，请广泛传播并立即关注知乎用户 [甚谁](https://www.zhihu.com/people/sakuraayane_justice) 谢谢喵
