from kubexec.templates import (
    create_job_template,
    create_shared_volume_mounts,
    create_shared_volumes,
)


def test_job_template_structure():
    job = create_job_template(
        "my-job", "alpine", ["/bin/bash", "-c", "echo hi"], namespace="ns",
        memory="2Gi", cpu="2", node_selector={"k": "v"},
    )
    pod = job["spec"]["template"]["spec"]
    container = pod["containers"][0]

    assert job["kind"] == "Job"
    assert job["metadata"]["name"] == "my-job"
    assert job["metadata"]["namespace"] == "ns"
    assert job["metadata"]["labels"]["app"] == "kubexec"
    assert container["name"] == "kubexec-container"
    assert container["image"] == "alpine"
    assert container["command"] == ["/bin/bash", "-c", "echo hi"]
    assert container["resources"]["limits"] == {"memory": "2Gi", "cpu": "2"}
    assert pod["restartPolicy"] == "Never"
    assert pod["nodeSelector"] == {"k": "v"}
    assert pod["automountServiceAccountToken"] is False
    assert job["spec"]["backoffLimit"] == 0


def test_job_template_optional_sections_only_when_given():
    job = create_job_template("j", "alpine", ["true"])
    pod = job["spec"]["template"]["spec"]
    assert "volumes" not in pod
    assert "nodeSelector" not in pod
    assert "volumeMounts" not in pod["containers"][0]

    job = create_job_template(
        "j", "alpine", ["true"],
        volumes=[{"name": "v"}], volume_mounts=[{"name": "v", "mountPath": "/v"}],
    )
    pod = job["spec"]["template"]["spec"]
    assert pod["volumes"] == [{"name": "v"}]
    assert pod["containers"][0]["volumeMounts"] == [{"name": "v", "mountPath": "/v"}]


def test_shared_volumes_and_mounts_are_consistent():
    volume_names = {v["name"] for v in create_shared_volumes()}
    mounts = {m["name"]: m for m in create_shared_volume_mounts()}

    assert set(mounts) == volume_names
    assert mounts["shared-team-volume"]["mountPath"] == "/shared/team"
    assert mounts["shared-public-volume"]["readOnly"] is True
