from .docs import build_openapi_spec, mount_api_docs, normalize_doc_targets
from .errors import install_request_id_hooks


class ResourceFactory:
    """Builds configured ``ApiResource`` subclasses and mounts their routes."""

    @classmethod
    def create(cls, base, **options):
        attrs = base._seed_options(options)
        resolved_name = base._resolve_resource_name(
            options.get('resource_name') or attrs.get('resource_name'),
            model=attrs.get('model'),
        )
        attrs.setdefault('resource_name', resolved_name)
        name = options.get('name') or (
            f'{resolved_name.title().replace("_", "")}ApiResource'
        )
        return type(name, (base,), attrs)

    @classmethod
    def register(cls, resource, app_or_bp, **mount_options):
        return resource._mount(app_or_bp, **mount_options)


class Api:
    """API manager bound to a Flask app or Blueprint.

    Usage::

        from flask_api import Api

        api = Api(
            api_bp,
            db=db,
            envelope_key='data',
            items_key='records',
            enable_docs=True,
            doc=['swagger', 'redoc'],
            docs={
                'title': 'FoodMart API',
                'version': '1.0.0',
                'description': 'REST API documentation',
            },
        )
        machinery = api.create_resource(model=Machinery, schema=MachinerySchema)
    """

    def __init__(self, app_or_bp=None, db=None, enable_docs=False,
                 docs=None, doc=None, doc_ui=None, **defaults):
        from .resource import ApiResource  # deferred: avoids import cycle

        self._resource_base = ApiResource
        self.app_or_bp = app_or_bp
        self.db = db
        self.enable_docs = bool(enable_docs or docs is not None or doc is not None or doc_ui is not None) if enable_docs is not False else False
        self.docs_config = dict(docs or {})
        raw_doc_targets = doc if doc is not None else doc_ui
        self.doc_targets = normalize_doc_targets(raw_doc_targets)
        self._docs_mounted = False

        self.errors = dict(defaults.pop('errors', {}) or {})
        self.defaults = {}
        if db is not None:
            self.defaults['db'] = db
        self.defaults.update(self._check_defaults(defaults))
        self.resources = []
        self._pending_resources = []

        if self.app_or_bp is not None:
            install_request_id_hooks(self.app_or_bp)
        if self.app_or_bp is not None and self.enable_docs:
            mount_api_docs(self, self.app_or_bp)

    @staticmethod
    def _check_defaults(defaults):
        """Validate Api-level defaults; unknown keys are configuration typos."""
        from .resource import ApiResource  # deferred: avoids import cycle

        unknown = sorted(
            key for key in defaults if key not in ApiResource.FACTORY_OPTIONS
        )
        if unknown:
            raise ValueError(
                f'Unknown Api option(s): {", ".join(unknown)}. '
                f'Allowed options: {", ".join(ApiResource.FACTORY_OPTIONS)}'
            )
        return {key: value for key, value in defaults.items() if value is not None}

    def init_app(self, app_or_bp, db=None, enable_docs=None,
                 docs=None, doc=None, doc_ui=None, **defaults):
        """Bind this ``Api`` instance to an app or blueprint and mount any
        resources created prior to ``init_app``."""
        self.app_or_bp = app_or_bp
        if db is not None:
            self.db = db
            self.defaults['db'] = db
        if docs is not None:
            self.docs_config.update(docs)
        if doc is not None or doc_ui is not None:
            self.doc_targets = normalize_doc_targets(doc if doc is not None else doc_ui)
        if enable_docs is not None:
            self.enable_docs = bool(enable_docs)
        elif docs is not None or doc is not None or doc_ui is not None:
            self.enable_docs = True

        if 'errors' in defaults:
            self.errors.update(defaults.pop('errors') or {})
        self.defaults.update(self._check_defaults(defaults))

        install_request_id_hooks(self.app_or_bp)
        if self.enable_docs:
            mount_api_docs(self, self.app_or_bp)

        while self._pending_resources:
            resource, mount_options = self._pending_resources.pop(0)
            if getattr(resource, 'db', None) is None and self.db is not None:
                resource.db = self.db
            resource.register(self.app_or_bp, **mount_options)
        return self

    def get_openapi_spec(self) -> dict:
        """Build and return the OpenAPI 3.0.3 specification dict for all registered resources."""
        return build_openapi_spec(self)

    def create_resource(self, **options):
        """Create a configured ``ApiResource`` subclass and mount it on ``app_or_bp``."""
        options['errors'] = {
            **self.errors,
            **(options.get('errors') or {}),
        }
        merged = {**self.defaults, **options}
        resource = self._resource_base.create_resource(**merged)
        resource.api_instance = self
        if resource not in self.resources:
            self.resources.append(resource)
        if self.app_or_bp is not None:
            resource.register(self.app_or_bp)
        else:
            self._pending_resources.append((resource, {}))
        return resource

    def create_resources(self, *resources, **options):
        """Create and mount one or more API resources."""
        if not resources and ('model' in options or 'schema' in options or 'schemas' in options):
            return self.create_resource(**options)

        if len(resources) == 1 and isinstance(resources[0], (list, tuple)):
            items = list(resources[0])
            return [self.create_resource(**{**options, **cfg}) for cfg in items]

        if resources:
            return [self.create_resource(**{**options, **cfg}) for cfg in resources]

        if options and all(isinstance(v, dict) for v in options.values()):
            created = {}
            for name, cfg in options.items():
                cfg_with_name = {'resource_name': name, **cfg}
                created[name] = self.create_resource(**cfg_with_name)
            return created

        raise ValueError(
            'create_resources() expects resource options (model=..., schema=... / schemas=...) '
            'or one or more resource configuration dictionaries.'
        )

    def register(self, resource, **mount_options):
        """Mount an existing ``ApiResource`` subclass on ``app_or_bp``."""
        resource.api_instance = self
        if resource not in self.resources:
            self.resources.append(resource)
        if getattr(resource, 'db', None) is None and self.db is not None:
            resource.db = self.db
        if self.app_or_bp is None:
            self._pending_resources.append((resource, mount_options))
        else:
            resource.register(self.app_or_bp, **mount_options)
        return self


ApiInstance = Api
