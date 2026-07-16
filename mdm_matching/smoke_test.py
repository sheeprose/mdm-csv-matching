from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
import yaml
from fastapi.testclient import TestClient

import mdm_matching.service as service


def main() -> None:
    workflow_path = Path("dify/mdm_workflow.yml")
    yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    batch_workflow_path = Path("dify/mdm_batch_review_workflow.yml")
    yaml.safe_load(batch_workflow_path.read_text(encoding="utf-8"))
    print("1. Dify DSL YAML 解析通过")

    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp_path = Path(tmp)
        os.environ["MDM_DATABASE_URL"] = f"sqlite:///{tmp_path / 'test_mdm.sqlite3'}"
        os.environ["PUBLIC_REVIEW_BASE_URL"] = "http://127.0.0.1:8000"
        os.environ["MDM_ALLOW_LOCAL_LLM_FALLBACK"] = "true"

        table_a = tmp_path / "tableA.csv"
        table_b = tmp_path / "tableB.csv"
        pd.DataFrame(
            [
                {"id": "a1", "title": "Microsoft Visio Standard 2007", "manufacturer": "Microsoft", "price": "129.95"},
                {"id": "a2", "title": "Adobe Photoshop Elements", "manufacturer": "Adobe Systems", "price": "89.99"},
            ]
        ).to_csv(table_a, index=False)
        pd.DataFrame(
            [
                {
                    "id": "b1",
                    "title": "microsoft visio standard 2007 version upgrade",
                    "manufacturer": "MS",
                    "price": "129.95",
                },
                {"id": "b2", "title": "QuickBooks 2007 Learning Guide", "manufacturer": "Intuit", "price": "38.99"},
            ]
        ).to_csv(table_b, index=False)

        client = TestClient(service.app)
        health = client.get("/health").json()
        assert health["ok"] is True
        print("2. API 健康检查通过")

        validate = client.post(
            "/validate-upload",
            json={"table_a_path": str(table_a), "table_b_path": str(table_b), "threshold": 0.9},
        ).json()
        assert validate["valid"] is True
        print("3. 上传校验通过")

        ingest = client.post(
            "/ingest-and-match",
            json={"table_a_path": str(table_a), "table_b_path": str(table_b), "threshold": 0.9},
        ).json()
        assert ingest["status"] in {"llm_required", "human_required", "llm_and_human_required", "completed"}
        assert "match_run_id" in ingest
        assert "all_candidate_pairs_sample" in ingest
        assert "routing_summary" in ingest
        assert "review_task" in ingest
        print(f"4. 摄入匹配通过，状态：{ingest['status']}")

        gray_plan = service.build_entity_cluster_plan(
            pd.DataFrame(
                [
                    {
                        "table1.id": "gray_a",
                        "table2.id": "gray_b",
                        "table1.title": "Gray Candidate",
                        "table2.title": "Gray Candidate Deluxe",
                        "table1.manufacturer": "Maker",
                        "table2.manufacturer": "Maker",
                        "table1.price": 100.0,
                        "table2.price": 180.0,
                        "confidence": 0.93,
                    }
                ]
            ),
            threshold=0.94,
        )
        assert len(gray_plan["auto_merged_clusters"]) == 0
        assert len(gray_plan["llm_conflict_clusters"]) == 1
        assert len([cluster for cluster in gray_plan["ignored_low_confidence_clusters"] if cluster.get("cluster_id") != "low_confidence_pairs"]) == 0
        print("4a. 灰区候选进入大模型判定，不再直接写成 active 独立实体")

        llm_batch = client.post(
            "/candidate-batch",
            json={"match_run_id": ingest["match_run_id"], "route": "llm", "offset": 0, "limit": 50},
        ).json()
        assert llm_batch["status"] in {"batch_ready", "batch_empty"}
        if llm_batch.get("clusters"):
            assert llm_batch.get("candidates") == []
        print(f"5. 冲突实体簇批次拉取通过：{llm_batch['returned_count']} 条")

        if llm_batch.get("clusters"):
            llm_result = {
                "clusters": [
                    {
                        "cluster_id": cluster["cluster_id"],
                        "entities": [
                            {
                                "canonical_title": cluster["records"][0]["title"],
                                "members": [record["node_id"] for record in cluster["records"]],
                                "aliases": [record["title"] for record in cluster["records"]],
                                "confidence": cluster.get("max_confidence", 0.95),
                                "decision": "same_entity",
                                "reason": "smoke test structured output",
                            }
                        ],
                    }
                    for cluster in llm_batch["clusters"]
                ]
            }
            process_all = client.post(
                "/commit-llm-batch",
                json={"match_run_id": ingest["match_run_id"], "offset": 0, "limit": 50, "llm_result": llm_result},
            ).json()
            assert process_all["status"] == "committed"
            assert process_all["committed_count"] == len(llm_batch["clusters"])
            print(f"6. 冲突实体簇结构化结果提交通过：committed={process_all['committed_count']}")
        else:
            process_all = client.post(
                "/process-all-high-confidence",
                json={"match_run_id": ingest["match_run_id"], "batch_size": 1},
            ).json()
            assert process_all["status"] == "no_high_confidence_required"
            print("6. 本次没有冲突实体簇，跳过大模型批量处理")

        initial_rebuild = client.post("/admin/rebuild-golden-records").json()
        initial_quality = client.get("/admin/entity-quality", params={"expected_table1": 2, "expected_table2": 2}).json()
        assert initial_rebuild["status"] == "rebuilt"
        assert initial_quality["valid"] is True
        assert initial_quality["table1_source_link_count"] == 2
        assert initial_quality["table2_source_link_count"] == 2
        print("6a. 初始导入 tableA/tableB 来源覆盖检查通过")

        synthetic_match_run_id = service.save_match_run(
            {
                "threshold": 0.9,
                "all_candidate_pairs_count": 2,
                "high_confidence_candidates": [
                    {
                        "table1.id": "a1",
                        "table2.id": "b1",
                        "table1.title": "Microsoft Visio Standard 2007",
                        "table2.title": "Microsoft Visio Standard 2007 Upgrade",
                        "table1.manufacturer": "Microsoft",
                        "table2.manufacturer": "MS",
                        "table1.price": 129.95,
                        "table2.price": 129.95,
                        "confidence": 0.95,
                    },
                    {
                        "table1.id": "a2",
                        "table2.id": "b2",
                        "table1.title": "Adobe Photoshop Elements",
                        "table2.title": "Adobe Photoshop Elements Retail",
                        "table1.manufacturer": "Adobe Systems",
                        "table2.manufacturer": "Adobe",
                        "table1.price": 89.99,
                        "table2.price": 89.99,
                        "confidence": 0.93,
                    },
                ],
                "human_review_candidates": [],
                "llm_commits": [],
            }
        )
        synthetic_process_all = client.post(
            "/process-all-high-confidence",
            json={"match_run_id": synthetic_match_run_id, "batch_size": 1},
        ).json()
        assert synthetic_process_all["status"] == "completed"
        assert synthetic_process_all["processed_batches"] == 2
        assert synthetic_process_all["committed_count"] == 2
        print("6b. 多批兼容候选循环处理通过：2 批")

        escaped_llm_result = (
            '{"records":[{"chosen_title":"Microsoft Visio Standard 2007 Upgrade",'
            '"chosen_record":{"table1.title":"Microsoft Visio Standard 2007",'
            '"table2.title":"Microsoft Visio Standard 2007 Upgrade",'
            '"table1.manufacturer":"Microsoft","table2.manufacturer":"MS",'
            '"table1.price":129.95,"table2.price":129.95,"confidence":0.95},'
            '"confidence":0.95,"reason":"double encoded smoke test"}]}'
        )
        escaped_match_run_id = service.save_match_run(
            {
                "threshold": 0.9,
                "all_candidate_pairs_count": 1,
                "high_confidence_candidates": [
                    {
                        "table1.title": "Microsoft Visio Standard 2007",
                        "table2.title": "Microsoft Visio Standard 2007 Upgrade",
                        "confidence": 0.95,
                    }
                ],
                "human_review_candidates": [],
                "llm_commits": [],
            }
        )
        escaped_commit = client.post(
            "/commit-llm-batch",
            json={"match_run_id": escaped_match_run_id, "offset": 0, "limit": 100, "llm_result": escaped_llm_result},
        ).json()
        assert escaped_commit["status"] == "committed"
        assert escaped_commit["titles"] == ["Microsoft Visio Standard 2007 Upgrade"]
        with service.get_engine().connect() as connection:
            selected_row = connection.execute(
                service.select(service.entities.c.selected_source_table, service.entities.c.status).where(
                    service.entities.c.canonical_title == "Microsoft Visio Standard 2007 Upgrade"
                )
            ).mappings().first()
        assert selected_row is not None
        assert selected_row["selected_source_table"] == "tableB"
        assert selected_row["status"] == "llm_active"
        print("6c. 大模型批次 JSON 字符串解析写库通过")

        invalid_cluster_match_run_id = service.save_match_run(
            {
                "threshold": 0.9,
                "all_candidate_pairs_count": 1,
                "llm_conflict_clusters": [
                    {
                        "cluster_id": "cluster_invalid",
                        "records": [
                            {"node_id": "table1:a1", "source_table": "table1", "source_id": "a1", "title": "A"},
                            {"node_id": "table2:b1", "source_table": "table2", "source_id": "b1", "title": "B"},
                        ],
                        "table1_ids": ["a1"],
                        "table2_ids": ["b1"],
                        "max_confidence": 0.95,
                    }
                ],
                "high_confidence_candidates": [],
                "human_review_candidates": [],
                "llm_commits": [],
            }
        )
        quality_before = client.get("/admin/entity-quality").json()
        bad_commit = client.post(
            "/commit-llm-batch",
            json={"match_run_id": invalid_cluster_match_run_id, "offset": 0, "limit": 100, "llm_result": '{"clusters":[{"cluster_id":"broken"'},
        ).json()
        quality_after = client.get("/admin/entity-quality").json()
        assert bad_commit["status"] == "retry_required"
        assert bad_commit["committed_count"] == 0
        assert quality_after["entity_count"] == quality_before["entity_count"]
        assert quality_after["uncertain_decision_count"] == quality_before["uncertain_decision_count"]
        assert quality_after["duplicate_source_link_count"] == 0
        print("6d. 不可解析大模型输出要求重试且不写 uncertain 通过")

        second_bad_commit = client.post(
            "/commit-llm-batch",
            json={"match_run_id": invalid_cluster_match_run_id, "offset": 0, "limit": 100, "llm_result": '{"clusters":[{"cluster_id":"broken"'},
        )
        third_bad_commit = client.post(
            "/commit-llm-batch",
            json={"match_run_id": invalid_cluster_match_run_id, "offset": 0, "limit": 100, "llm_result": '{"clusters":[{"cluster_id":"broken"'},
        )
        assert second_bad_commit.status_code == 200
        assert third_bad_commit.status_code == 200
        assert third_bad_commit.json()["status"] == "invalid_llm_output_stopped"
        print("6d1. 连续无效大模型输出不再抛 HTTP 422，通过结构化状态停止")

        extra_cluster_match_run_id = service.save_match_run(
            {
                "threshold": 0.9,
                "all_candidate_pairs_count": 1,
                "llm_conflict_clusters": [
                    {
                        "cluster_id": "cluster_expected",
                        "records": [
                            {"node_id": "table1:x1", "source_table": "table1", "source_id": "x1", "title": "Expected A"},
                            {"node_id": "table2:y1", "source_table": "table2", "source_id": "y1", "title": "Expected B"},
                        ],
                        "table1_ids": ["x1"],
                        "table2_ids": ["y1"],
                        "max_confidence": 0.95,
                    }
                ],
                "high_confidence_candidates": [],
                "human_review_candidates": [],
                "llm_commits": [],
            }
        )
        extra_commit = client.post(
            "/commit-llm-batch",
            json={
                "match_run_id": extra_cluster_match_run_id,
                "offset": 0,
                "limit": 30,
                "llm_result": {
                    "clusters": [
                        {
                            "cluster_id": "cluster_expected",
                            "entities": [
                                {
                                    "canonical_title": "Expected A",
                                    "members": ["table1:x1", "table2:y1"],
                                    "aliases": ["Expected B"],
                                    "confidence": 0.95,
                                    "decision": "same_entity",
                                    "reason": "expected cluster",
                                }
                            ],
                        },
                        {
                            "cluster_id": "cluster_extra",
                            "entities": [
                                {
                                    "canonical_title": "Extra",
                                    "members": ["table1:extra"],
                                    "aliases": [],
                                    "confidence": 0.5,
                                    "decision": "same_entity",
                                    "reason": "should be ignored",
                                }
                            ],
                        },
                    ]
                },
            },
        ).json()
        assert extra_commit["status"] == "committed"
        assert extra_commit["committed_count"] == 1
        print("6d2. 大模型额外输出非当前批次 cluster_id 时忽略额外簇通过")

        partial_cluster_match_run_id = service.save_match_run(
            {
                "threshold": 0.9,
                "all_candidate_pairs_count": 3,
                "llm_conflict_clusters": [
                    {
                        "cluster_id": "cluster_prev",
                        "records": [
                            {"node_id": "table1:p1", "source_table": "table1", "source_id": "p1", "title": "Previous A"},
                            {"node_id": "table2:p2", "source_table": "table2", "source_id": "p2", "title": "Previous B"},
                        ],
                        "table1_ids": ["p1"],
                        "table2_ids": ["p2"],
                        "max_confidence": 0.95,
                    },
                    {
                        "cluster_id": "cluster_current_1",
                        "records": [
                            {"node_id": "table1:c1", "source_table": "table1", "source_id": "c1", "title": "Current One A"},
                            {"node_id": "table2:c2", "source_table": "table2", "source_id": "c2", "title": "Current One B"},
                        ],
                        "table1_ids": ["c1"],
                        "table2_ids": ["c2"],
                        "max_confidence": 0.95,
                    },
                    {
                        "cluster_id": "cluster_current_2",
                        "records": [
                            {"node_id": "table1:c3", "source_table": "table1", "source_id": "c3", "title": "Current Two A"},
                            {"node_id": "table2:c4", "source_table": "table2", "source_id": "c4", "title": "Current Two B"},
                        ],
                        "table1_ids": ["c3"],
                        "table2_ids": ["c4"],
                        "max_confidence": 0.95,
                    },
                ],
                "high_confidence_candidates": [],
                "human_review_candidates": [],
                "llm_commits": [],
            }
        )
        partial_commit = client.post(
            "/commit-llm-batch",
            json={
                "match_run_id": partial_cluster_match_run_id,
                "offset": 1,
                "limit": 2,
                "llm_result": {
                    "clusters": [
                        {
                            "cluster_id": "cluster_prev",
                            "entities": [
                                {
                                    "canonical_title": "Previous A",
                                    "members": ["table1:p1", "table2:p2"],
                                    "aliases": ["Previous B"],
                                    "confidence": 0.95,
                                    "decision": "same_entity",
                                    "reason": "previous cluster should be ignored",
                                }
                            ],
                        },
                        {
                            "cluster_id": "cluster_current_1",
                            "entities": [
                                {
                                    "canonical_title": "Current One A",
                                    "members": ["table1:c1", "table2:c2"],
                                    "aliases": ["Current One B"],
                                    "confidence": 0.95,
                                    "decision": "same_entity",
                                    "reason": "first current cluster is covered",
                                }
                            ],
                        },
                    ]
                },
            },
        ).json()
        assert partial_commit["status"] == "partial_committed"
        assert partial_commit["committed_count"] == 1
        assert partial_commit["next_offset"] == 2
        assert partial_commit["has_more"] is True
        print("6d3. 大模型返回上一批加当前批部分结果时安全部分提交通过")

        duplicate_commit = client.post(
            "/commit-llm-batch",
            json={"match_run_id": escaped_match_run_id, "offset": 0, "limit": 100, "llm_result": escaped_llm_result},
        ).json()
        assert duplicate_commit["status"] == "already_committed"
        print("6e. 重复批次幂等保护通过")

        think_match_run_id = service.save_match_run(
            {
                "threshold": 0.9,
                "all_candidate_pairs_count": 1,
                "llm_conflict_clusters": [
                    {
                        "cluster_id": "cluster_think",
                        "records": [
                            {"node_id": "table1:t1", "source_table": "table1", "source_id": "t1", "title": "Think Test A"},
                            {"node_id": "table2:t2", "source_table": "table2", "source_id": "t2", "title": "Think Test B"},
                        ],
                        "table1_ids": ["t1"],
                        "table2_ids": ["t2"],
                        "max_confidence": 0.96,
                    }
                ],
                "high_confidence_candidates": [],
                "human_review_candidates": [],
                "llm_commits": [],
            }
        )
        think_result = (
            '<think>这里是模型推理过程，不能写入数据库。</think>'
            '{"clusters":[{"cluster_id":"cluster_think","entities":[{'
            '"canonical_title":"Think Test A","members":["table1:t1","table2:t2"],'
            '"aliases":["Think Test B"],"confidence":0.96,"decision":"same_entity","reason":"test"}]}]}'
        )
        think_commit = client.post(
            "/commit-llm-batch",
            json={"match_run_id": think_match_run_id, "offset": 0, "limit": 100, "llm_result": think_result},
        ).json()
        assert think_commit["status"] == "committed"
        assert think_commit["titles"] == ["Think Test A"]
        assert not any("<think>" in title for title in think_commit["titles"])
        print("6f. 大模型 think 标签清理和 JSON 提取通过")

        review = ingest["review_task"]
        assert review["status"] in {"review_created", "no_review_required"}
        print(f"7. 摄入阶段人工审核任务检查通过：{review['status']}")

        if review["status"] == "review_created":
            page = client.get(f"/review/{review['task_id']}")
            assert page.status_code == 200
            print("8. 人工审核页面打开通过")
            submit_payload = {f"choice_{index}": "table2" for index, _ in enumerate(ingest["human_review_candidates"])}
            submit = client.post(f"/review/{review['task_id']}", data=submit_payload)
            assert submit.status_code == 200
            print("9. 人工审核分页提交写库通过")
        else:
            print("8. 本次没有低置信候选，跳过人工审核页面和提交")

        rebuild = client.post("/admin/rebuild-golden-records").json()
        quality = client.get("/admin/entity-quality").json()
        assert rebuild["status"] == "rebuilt"
        assert quality["valid"] is True
        assert quality["entity_count"] == quality["golden_records_count"]
        assert quality["duplicate_golden_entity_id_count"] == 0
        assert quality["duplicate_source_link_count"] == 0
        print(f"9. 唯一实体表质量检查通过：entities={quality['entity_count']}")
        if service._engine is not None:
            service._engine.dispose()
            service._engine = None

    print("本地自检完成")


if __name__ == "__main__":
    main()
