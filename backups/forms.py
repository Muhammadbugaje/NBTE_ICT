import re

from django import forms

from .models import BackupDestination

PREFIX_RE = re.compile(r"^[A-Z][A-Z0-9_]*_$")
SECRETISH = re.compile(r"(secret|password|access[_-]?key|token)\s*[=:]", re.I)


def suggested_prefix(name):
    slug = re.sub(r"[^A-Z0-9]+", "_", (name or "").upper()).strip("_") or "PRIMARY"
    return f"BACKUP_S3_{slug}_"


class DestinationForm(forms.Form):
    name = forms.CharField(max_length=80, widget=forms.TextInput(attrs={"class": "input", "autocomplete": "off"}),
                           help_text="A name people will recognise, e.g. “Office USB drive”.")
    kind = forms.ChoiceField(choices=BackupDestination.KIND_CHOICES)
    path = forms.CharField(required=False, max_length=500, label="Folder on this server",
                           help_text="A full path, e.g. /var/backups/nbte. Best on a different disk from the system.")
    mount_path = forms.CharField(required=False, max_length=500, label="Drive",
                                 help_text="Where the drive is mounted, e.g. /media/ict/BACKUP.")
    subfolder = forms.CharField(required=False, max_length=120, initial="nbte-backups", label="Folder on the drive")
    bucket = forms.CharField(required=False, max_length=200)
    prefix = forms.CharField(required=False, max_length=200, label="Folder in the bucket", initial="nbte")
    region = forms.CharField(required=False, max_length=60,
                             help_text="AWS: e.g. eu-west-2. Cloudflare R2: auto. Supabase: your project's region.")
    endpoint_url = forms.URLField(required=False, max_length=300, label="Endpoint URL", assume_scheme="https",
                                  help_text="Blank for AWS S3. Required for R2, Supabase and Backblaze B2.")
    addressing_style = forms.ChoiceField(required=False, initial="auto", label="Addressing style",
                                         widget=forms.Select(attrs={"class": "input"}), choices=[
        ("auto", "Automatic"), ("path", "Path (Supabase, MinIO)"), ("virtual", "Virtual host")])
    credential_env_prefix = forms.CharField(required=False, max_length=60, label="Credential prefix",
                                            help_text="The keys go in the server's .env under this prefix.")
    is_active = forms.BooleanField(required=False, label="Use this destination for backups")

    def __init__(self, *args, instance=None, **kwargs):
        self.instance = instance
        if instance is not None and not kwargs.get("data") and not args:
            c = instance.config or {}
            kwargs["initial"] = {
                "name": instance.name, "kind": instance.kind, "is_active": instance.is_active,
                "credential_env_prefix": instance.credential_env_prefix,
                "path": c.get("path", ""), "mount_path": c.get("mount_path", ""),
                "subfolder": c.get("subfolder", "nbte-backups"), "bucket": c.get("bucket", ""),
                "prefix": c.get("prefix", ""), "region": c.get("region", ""),
                "endpoint_url": c.get("endpoint_url", ""), "addressing_style": c.get("addressing_style", "auto"),
            }
        super().__init__(*args, **kwargs)
        self.auto_id = f"dest-{instance.pk if instance else 'new'}-%s"

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        qs = BackupDestination.objects.filter(name__iexact=name)
        if self.instance is not None:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("Another destination already has this name.")
        return name

    def clean(self):
        data = super().clean()
        kind = data.get("kind")
        for field in ("path", "mount_path", "subfolder", "bucket", "prefix", "region", "endpoint_url"):
            value = (data.get(field) or "").strip()
            data[field] = value
            userinfo = field == "endpoint_url" and "@" in value.split("//")[-1].split("/")[0]
            if SECRETISH.search(value) or userinfo:
                self.add_error(field, "Do not put keys or passwords here. Credentials go in the server's .env.")
        if kind == BackupDestination.LOCAL:
            if not data["path"]:
                self.add_error("path", "Enter the folder to write backups to.")
            elif not data["path"].startswith("/"):
                self.add_error("path", "Use a full path that starts with /.")
        elif kind == BackupDestination.MOUNTED:
            if not data["mount_path"]:
                self.add_error("mount_path", "Choose the drive.")
            elif not data["mount_path"].startswith("/"):
                self.add_error("mount_path", "Use a full path that starts with /.")
            if "/" in data["subfolder"].strip("/") or ".." in data["subfolder"]:
                self.add_error("subfolder", "Use a single folder name, without slashes.")
        elif kind == BackupDestination.S3:
            if not data["bucket"]:
                self.add_error("bucket", "Enter the bucket name.")
            prefix = (data.get("credential_env_prefix") or "").strip().upper()
            if not prefix:
                prefix = suggested_prefix(data.get("name"))
            if not prefix.endswith("_"):
                prefix += "_"
            if not PREFIX_RE.match(prefix):
                self.add_error("credential_env_prefix",
                               "Use capital letters, digits and underscores, ending with _, e.g. BACKUP_S3_PRIMARY_.")
            data["credential_env_prefix"] = prefix
        if kind != BackupDestination.S3:
            data["credential_env_prefix"] = ""
        return data

    def config(self):
        d, kind = self.cleaned_data, self.cleaned_data["kind"]
        if kind == BackupDestination.LOCAL:
            return {"path": d["path"]}
        if kind == BackupDestination.MOUNTED:
            return {"mount_path": d["mount_path"].rstrip("/") or "/", "subfolder": d["subfolder"].strip("/")}
        return {"bucket": d["bucket"], "prefix": d["prefix"].strip("/"), "region": d["region"],
                "endpoint_url": d["endpoint_url"], "addressing_style": d.get("addressing_style") or "auto"}

    def save(self):
        d = self.cleaned_data
        dest = self.instance or BackupDestination()
        dest.name, dest.kind, dest.config = d["name"], d["kind"], self.config()
        dest.credential_env_prefix = d["credential_env_prefix"]
        dest.is_active = d["is_active"]
        dest.is_placeholder = False
        dest.save()
        return dest
