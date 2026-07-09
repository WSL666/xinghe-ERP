# vibe-engine-server — 目录结构

> 自动生成于 2026-07-09

---

## 根目录文件

```
.DS_Store
.gitattributes
.gitignore
.gitlab-ci.yml
.gitmodules
build.sh
compile_pb.sh
config_vibe.py
debugger_main.py
Dockerfile
GRPC_README.md
miniconda.sh
monitor.txt
nacos_diff.py
README.md
requirements.txt
requirements_imagegeneration.txt
requirements_imagegeneraton_fix.txt
requirements_videogeneration.txt
rpui_video.log
runner_feed_prod.py
runner_infograph.py
runner_main.py
runner_main_mt.py
server_main.py
server_tools.py
test.json
test_summary.json
```

---

## .cursor/

```
.cursor/
└── rules/
    ├── 1-strictly-n.mdc
    └── howtodevelop.mdc
```

---

## .gitlab/

```
.gitlab/
└── merge_request_templates/
    └── feat_template.md
```

---

## agent/

```
agent/
├── __init__.py
├── agent_defs.py
├── agent_user_request_param.py
├── chat_agent_system.py
├── event_handler.py
├── find_image_agent_system.py
├── find_image_tools.py
├── gemini_writer_agent_system.py
├── imagegeneration_agent_system.py
├── imagegeneration_config.json
├── imagegeneration_tools.py
├── infograph_agent_system.py
├── infograph_tools.py
├── master_agent_context.py
├── master_agent_init.py
├── master_agent_system.py
├── onlyside_chat_video_agent.py
├── redis_traj_persister.py
├── research_agent_system.py
├── research_tools.py
├── tools.py
├── topic_generation_agent_system.py
├── topic_generation_tools.py
├── videogeneration_tools.py
├── workspace_init.py
├── writer_agent_system.py
├── writer_tools.py
└── __pycache__/
    ├── agent_user_request_param.cpython-311.pyc
    ├── imagegeneration_agent_system.cpython-311.pyc
    └── __init__.cpython-311.pyc
```

---

## bgm_library/

```
bgm_library/
├── .DS_Store
├── bgm.jsonl
├── bgm_v2.jsonl
├── bgm_v3.jsonl
├── produce_audio.py
├── readme.txt
├── tag_hierarchy.json
└── preprocess_bgm_tools/
    ├── bgm_cls_api_gettoken.py
    ├── bgm_cls_api_run.py
    ├── bgm_cls_results_add_localbgm.py
    ├── bgm_cls_results_convert_jsonl.py
    ├── bgm_cls_result_add_duration.py
    ├── bgm_cls_result_range.py
    ├── bgm_sound.py
    ├── bgm_upload_cos.py
    └── readme.md
```

---

## configs/

```
configs/
└── dfvertex-tt7-5e0b4135fce7.json
```

---

## docs/

```
docs/
├── tool.md
└── tool_dev_rules.md
```

---

## eval_kit/

```
eval_kit/
├── eval_runner.py
└── viewer/
    ├── .gitignore
    ├── index.html
    ├── package-lock.json
    ├── package.json
    ├── requirements.txt
    ├── server.py
    ├── start-viewer.sh
    ├── vite.config.js
    └── src/
        ├── main.jsx
        └── viewer.jsx
```

---

## frontend/

```
frontend/
├── system_prompt/
│   ├── build_prompt.py
│   ├── export_json.py
│   ├── parse_from_xml.py
│   ├── structure.json
│   └── content/
│       ├── about_mumu.md
│       ├── additional_context.md
│       ├── article_canvas.md
│       ├── ask_for_confirmation.md
│       ├── canvas_types.md
│       ├── context_assert_completeness.md
│       ├── context_examples.md
│       ├── context_sources.md
│       ├── context_source_external_research.md
│       ├── context_source_mumu_brief.md
│       ├── context_source_user_task.md
│       ├── context_source_workspace.md
│       ├── creation.md
│       ├── creation_context.md
│       ├── creation_design.md
│       ├── creation_video.md
│       ├── creation_workspace_first.md
│       ├── creation_write.md
│       ├── design_four_governing_principles.md
│       ├── design_four_principles.md
│       ├── design_text_led.md
│       ├── design_text_led_design.md
│       ├── design_visual_led.md
│       ├── design_visual_led_design.md
│       ├── drafting_in_sections.md
│       ├── example_one.md
│       ├── example_three.md
│       ├── example_two.md
│       ├── intelligent_navigation.md
│       ├── invoke_subagents.md
│       ├── long_form.md
│       ├── mumu_brief.md
│       ├── outlining_first.md
│       ├── parrallization.md
│       ├── project_canvas.md
│       ├── recall_before_write.md
│       ├── research_protocols.md
│       ├── respond_to_user.md
│       ├── review_and_refine.md
│       ├── root.md
│       ├── short_form.md
│       ├── the_fifth_component.md
│       ├── the_first_component.md
│       ├── the_fourth_component.md
│       ├── the_second_component.md
│       ├── the_third_component.md
│       ├── tone_and_formatting.md
│       ├── tool_call.md
│       ├── using_todo_markers.md
│       ├── workspace_canvas_types.md
│       ├── workspace_intelligent_navigation.md
│       ├── write_default_style.md
│       ├── write_drafting.md
│       └── write_emulate_human_prose.md
└── system_prompt_dev/
    ├── index.html
    ├── package-lock.json
    ├── package.json
    ├── start.sh
    ├── vite.config.js
    ├── public/
    │   └── prompt_data.json
    ├── scripts/
    │   └── parse_prompt.py
    └── src/
        ├── main.jsx
        ├── prompt_data.json
        └── system.jsx
```

---

## hooks/

```
hooks/
├── __init__.py
├── hook_matcher.py
└── hook_registry.py
```

---

## nacos-data/

```
nacos-data/
└── snapshot/
    ├── rockagent_llm_model_center+DEFAULT_GROUP+2597be46-34fe-4439-a320-e5b0be021c27
    └── rockagent_muset_agents+DEFAULT_GROUP+2597be46-34fe-4439-a320-e5b0be021c27
```

---

## protos/

```
protos/
├── __init__.py
├── assets_service.proto
├── assets_service_pb2.py
├── assets_service_pb2.pyi
├── assets_service_pb2_grpc.py
├── generic_service.proto
├── generic_service_pb2.py
├── generic_service_pb2.pyi
├── generic_service_pb2_grpc.py
├── goliath.proto
├── goliath_pb2.py
├── goliath_pb2.pyi
├── goliath_pb2_grpc.py
├── metering_service.proto
├── metering_service_pb2.py
├── metering_service_pb2.pyi
├── metering_service_pb2_grpc.py
├── platform_service.proto
├── platform_service.py
├── platform_service_pb2.py
├── platform_service_pb2_grpc.py
├── proto_response_utils.py
├── request_log.proto
├── request_log_pb2.py
├── request_log_pb2.pyi
├── request_log_pb2_grpc.py
└── __pycache__/
    └── (各 .pyc 编译文件)
```

---

## rockagent/

```
rockagent/
├── __init__.py
├── env.py
├── cli/
│   ├── debugger.py
│   └── runner.py
├── config/
│   ├── agent_factory.py
│   ├── config_factory.py
│   ├── config_key_decorator.py
│   ├── model_factory.py
│   ├── nacos_client.py
│   └── tools_factory.py
├── core/
│   ├── __init__.py
│   ├── agent.py
│   ├── base_agent.py
│   ├── context.py
│   ├── executor.py
│   ├── model_context.py
│   └── traj_persister.py
├── debugger/
│   └── main.py
├── helpers/
│   ├── __init__.py
│   ├── message_helpers.py
│   └── tool_helpers.py
├── llm/
│   ├── claude.py
│   ├── gemini.py
│   ├── mocked_llm.py
│   ├── openai.py
│   ├── openai_responses.py
│   ├── utils.py
│   └── image/
│       ├── __init__.py
│       ├── image_generation.py
│       └── image_llm_client.py
├── model/
│   ├── __init__.py
│   ├── agent.py
│   ├── base.py
│   ├── brain.py
│   ├── event.py
│   ├── exceptions.py
│   └── tools.py
├── perf/
│   └── perf.py
├── server/
│   ├── handler.py
│   ├── main.py
│   ├── request_context.py
│   └── protos/
│       ├── __init__.py
│       ├── platform_service.proto
│       ├── platform_service.py
│       ├── platform_service_pb2.py
│       ├── platform_service_pb2.pyi
│       ├── platform_service_pb2_grpc.py
│       ├── platform_service_server.py
│       └── proto_response_utils.py
├── telemetry/
│   ├── base.py
│   ├── context.py
│   └── tracing.py
├── tool/
│   ├── __init__.py
│   ├── cli.py
│   ├── tool_base.py
│   ├── tool_error.py
│   └── tool_result.py
└── utils/
    ├── cos_utils.py
    ├── event_loop_executor.py
    ├── llm_utils.py
    ├── log_utils.py
    ├── redis_utils.py
    ├── tool_utils.py
    └── utils.py
```

---

## tests/

```
tests/
├── __init__.py
├── agent_user_request_param_test.py
├── test_client.py
├── test_json_req.py
├── test_requests.py
├── agent/
│   ├── eval_fewshot_end2end_writing.py
│   ├── eval_master_agent.py
│   ├── eval_spec_agent.py
│   ├── eval_spec_and_master.py
│   ├── test_master_agent_conversations.py
│   ├── test_master_agent_newquery.py
│   ├── test_onlyside_chat_video_from_json.py
│   └── tools_helper_test.py
├── call_llm/
│   ├── test_utils.py
│   └── test_ys_oneapi.py
├── context/
│   └── test_agent_context.py
├── feed/
│   └── csv_to_jsonl.py
├── image/
│   ├── test_process_image.py
│   ├── prompt_templates/
│   │   ├── content_image.md
│   │   ├── cover_image.md
│   │   └── cover_image_ratio.md
│   └── peitu_article_v2/
│       ├── caption_doubao.py
│       ├── cover_system_prompt.txt
│       ├── generate_prompt.py
│       └── article_0/ ~ article_149/   (共 150 个文章目录，每个包含:)
│           ├── article.json
│           ├── article.md
│           ├── article_with_image.json
│           ├── result_cover.json
│           └── 图片N.jpg  (若干张图片)
├── infographs/
│   ├── test_evaluate_image.py
│   ├── test_fix_code.py
│   ├── test_generate_code.py
│   ├── test_infographs.py
│   ├── test_match_image.py
│   └── test_renderer.py
├── research_agent/
│   ├── gemini_search.py
│   ├── gemini_search_result_parse.py
│   ├── gemini_test.sample.json
│   ├── time_stat.py
│   └── tikhub/
│       ├── twitter_get_user_post.json
│       └── twitter_get_user_posts.txt
├── results/
│   ├── gr_demo.py
│   ├── print.py
│   └── .gradio/
│       └── certificate.pem
├── review_tool/
│   ├── req.json
│   ├── runner_test_request.py
│   ├── test_qwen3_vl_direct.py
│   └── test_review_tool.py
├── rockflow_grpc/
│   ├── main.py
│   └── test_client.output.txt
├── shadow_workspace/
│   └── test_shadow_workspace.py
└── video_tip/
    ├── input.json
    ├── run.py
    └── test_result.json
```

---

## tools/

```
tools/
├── .DS_Store
├── __init__.py
├── canvas.py
├── custom_scraping.py
├── finish_research_with_report.py
├── functional_utils.py
├── gemini_writer.py
├── image_finding.py
├── imagegeneration.py
├── imagegeneration_base.py
├── imagegeneration_evaluator.py
├── imagegeneration_img2img.py
├── imagegeneration_text2img.py
├── img_analyzing.py
├── img_searching.py
├── infograph.py
├── infograph_base.py
├── infograph_code_editor.py
├── infograph_code_generator.py
├── infograph_image_evaluator.py
├── infograph_image_matcher.py
├── jina_scraping.py
├── langchain_filemanagement.py
├── nano_banana_pro.py
├── python_sandbox.py
├── readme.md
├── read_webpage.py
├── read_youtube_transcript.py
├── research.py
├── review_registry.py
├── review_tool.py
├── review_tool_config.json
├── scratchpad.py
├── seedream.py
├── seedream_image.py
├── semrush.py
├── tikhub.py
├── tikhub_utils.py
├── todo_write.py
├── video_frame_review.py
├── video_frame_review_prompts.py
├── video_sora2_deal.py
├── videogeneration_add_bgm_v2.py
├── videogeneration_add_lines.py
├── videogeneration_add_narration.py
├── videogeneration_add_subtitle.py
├── videogeneration_base.py
├── videogeneration_call_grok.py
├── videogeneration_call_hailuo_tts.py
├── videogeneration_call_hailuo_tts_sync.py
├── videogeneration_call_huoshan_tts.py
├── videogeneration_call_seed_dance.py
├── videogeneration_call_wan.py
├── videogeneration_concat.py
├── videogeneration_concat_utils_v4.py
├── videogeneration_firstframe.py
├── videogeneration_remove_humanvoice.py
├── videogeneration_response_parse.py
├── videogeneration_sora2api.py
├── videogeneration_utilts.py
├── videogeneration_video.py
├── wan_image.py
├── web_scraping.py
├── web_searching.py
├── workspace_tool.py
├── writer.py
├── MiniLM/
│   ├── .gitattributes
│   ├── config.json
│   ├── config_sentence_transformers.json
│   ├── data_config.json
│   ├── model.safetensors
│   ├── modules.json
│   ├── pytorch_model.bin
│   ├── README.md
│   ├── rust_model.ot
│   ├── sentence_bert_config.json
│   ├── special_tokens_map.json
│   ├── tf_model.h5
│   ├── tokenizer.json
│   ├── tokenizer_config.json
│   ├── vocab.txt
│   └── 1_Pooling/
│       └── config.json
├── model/
│   ├── __init__.py
│   ├── analyze_image_result.py
│   ├── canvas_tool_result.py
│   ├── find_image_result.py
│   ├── video_generator_result.py
│   └── (共 35 个 .py result model 文件)
├── shadow_workspace/
│   ├── __init__.py
│   ├── model.py
│   ├── shadow_workspace.py
│   ├── shadow_workspace_base.py
│   └── utils.py
├── skills/
│   ├── __init__.py
│   ├── creation_advertisement_script.py
│   ├── creation_illustration.py
│   ├── creation_novel_adaptation.py
│   ├── creation_video.py
│   ├── creation_video_story.py
│   ├── creation_visual.py
│   ├── creation_writing.py
│   ├── seo_toolkit.py
│   ├── skills.py
│   ├── social_media_toolkit.py
│   ├── novel_resources/
│   │   ├── __init__.py
│   │   ├── novel_adapt_method.py
│   │   └── novel_output_style.py
│   └── video/
│       ├── __init__.py
│       ├── detailed_plot_development.py
│       ├── frame_generation_and_verification.py
│       ├── generation_and_postprocess.py
│       ├── ip_library.py
│       ├── mandatory_lines_check.py
│       ├── mandatory_narration_check.py
│       ├── storyboard_development.py
│       └── videoclip_decomposition.py
└── video_analysis/
    ├── __init__.py
    ├── analyzer.py
    ├── backend_google.py
    ├── backend_oneapi.py
    ├── config.py
    ├── prompts.py
    └── video_processor.py
```

---

## utils/

```
utils/
├── __init__.py
├── asset_client.py
├── blocking_monitor.py
├── call_llm_tasks.py
├── convert_test_data.py
├── downloader.py
├── extract.py
├── gen_citecode.py
├── genmini.py
├── gpt_api.py
├── io_checker.py
├── message.py
├── qwen3.py
├── readme.md
├── str_utils.py
├── url_format.py
├── utils.py
├── ys_oneapi.py
├── onlyside_chat_video/
│   ├── __init__.py
│   ├── input.json
│   ├── server.py
│   ├── server_oneapi.py
│   └── test_server.py
├── resource_queue/
│   ├── api_server.py
│   ├── client.py
│   ├── config.py
│   ├── grafana_dashboard.json
│   ├── lua_scripts.py
│   ├── metrics.py
│   ├── process_worker.py
│   ├── README.md
│   ├── redis_client.py
│   ├── resource_config_manager.py
│   ├── task_manager.py
│   └── test/
│       ├── test_api_server.py
│       └── test_client.py
├── rpui_video/
│   ├── __init__.py
│   ├── input.json
│   ├── output-.json
│   ├── output-_export.xlsx
│   ├── output.json
│   ├── output1.json
│   ├── output_export.xlsx
│   ├── output_merged_by_request_id.xlsx
│   ├── output_new_old_merged_by_request_id.xlsx
│   ├── output_selected_fields.xlsx
│   ├── rpui_video.log
│   ├── server_new.py
│   ├── server_old.py
│   ├── test_rpui_create_video.py
│   └── test_server.py
├── slight_movement/
│   ├── server.py
│   ├── test_server.py
│   └── test_slight_movement.py
├── timbre_clone/
│   ├── __init__.py
│   ├── app.py
│   ├── README_STANDALONE.md
│   ├── requirements.txt
│   ├── audios/
│   │   └── linzhiling_3-7s.mp3
│   ├── core/
│   │   ├── __init__.py
│   │   └── voice_clone_unifed_v3_async.py
│   ├── demos/
│   │   ├── __init__.py
│   │   └── tts_standalone.py
│   ├── engines/
│   │   ├── __init__.py
│   │   ├── tts_indextts2.py
│   │   ├── tts_minimax.py
│   │   └── tts_qwen3.py
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── audio_recognizer.py
│   │   ├── audio_utils.py
│   │   ├── cos_uploader.py
│   │   └── language_translate.py
│   └── web/
│       └── templates/
│           └── index.html
├── tokenizers/
│   └── qwen_tokenizer/
│       ├── added_tokens.json
│       ├── merges.txt
│       ├── special_tokens_map.json
│       ├── tokenizer.json
│       ├── tokenizer_config.json
│       └── vocab.json
├── tool_server/
│   ├── __init__.py
│   ├── class_loader.py
│   └── config.py
└── video_tip/
    ├── API.md
    ├── input.json
    ├── run.py
    ├── server.py
    ├── server_oneapi.py
    └── test_server.py
```

---

## __pycache__/

```
__pycache__/
└── config_vibe.cpython-311.pyc
```

---

## 目录总览

| 目录 | 说明 |
|------|------|
| `.cursor/` | Cursor AI 规则配置 |
| `.gitlab/` | GitLab MR 模板 |
| `agent/` | 各类 Agent 系统实现（master、chat、research、writer、image、video、infograph） |
| `bgm_library/` | 背景音乐数据（JSONL 目录 + 预处理工具） |
| `configs/` | GCP 服务账号 JSON |
| `docs/` | 工具开发文档 |
| `eval_kit/` | 评测运行器 + React 可视化界面 |
| `frontend/` | 系统提示词内容（Markdown）+ React 开发环境 |
| `hooks/` | Hook 匹配器与注册表 |
| `nacos-data/` | Nacos 配置快照 |
| `protos/` | gRPC proto 定义 + Python 生成桩代码 |
| `rockagent/` | 核心 Agent 框架（LLM 客户端、配置、服务器、工具、遥测） |
| `tests/` | 测试套件（含 150 篇文章图像测试语料） |
| `tools/` | 所有工具实现（视频生成、图像生成、搜索、抓取、canvas 等） |
| `utils/` | 通用工具模块（LLM 客户端、视频流水线、音色克隆、资源队列） |
