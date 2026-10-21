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
            errors={
                404: lambda message: {'message': message},
                ValidationError: lambda errors: {'errors': errors},
            },
        )
        machinery = api.create_resource(model=Machinery, schema=MachinerySchema)

    Also supports deferred initialization via ``init_app`` and batch creation
    via ``create_resources``::

        api = Api()
        api.init_app(api_bp, db=db, envelope_key='data')

        # Single or batch resource creation:
        items_res = api.create_resources(model=Item, schema=ItemSchema)
        cat_res, item_res = api.create_resources(
            {'model': Category, 'schema': CategorySchema},
            {'model': Item, 'schema': ItemSchema},
        )
    """

    def __init__(self, app_or_bp=None, db=None, **defaults):
        from .resource import ApiResource  # deferred: avoids import cycle

        self._resource_base = ApiResource
        self.app_or_bp = app_or_bp
        self.db = db
        self.errors = dict(defaults.pop('errors', {}) or {})
        self.defaults = {}
        if db is not None:
            self.defaults['db'] = db
        for key, value in defaults.items():
            if value is not None and key in ApiResource.FACTORY_OPTIONS:
                self.defaults[key] = value
        self._pending_resources = []

    def init_app(self, app_or_bp, db=None, **defaults):
        """Bind this ``Api`` instance to an app or blueprint and mount any
        resources created prior to ``init_app``."""
        self.app_or_bp = app_or_bp
        if db is not None:
            self.db = db
            self.defaults['db'] = db
        if 'errors' in defaults:
            self.errors.update(defaults.pop('errors') or {})
        for key, value in defaults.items():
            if value is not None and key in self._resource_base.FACTORY_OPTIONS:
                self.defaults[key] = value

        while self._pending_resources:
            resource, mount_options = self._pending_resources.pop(0)
            if getattr(resource, 'db', None) is None and self.db is not None:
                resource.db = self.db
            resource.register(self.app_or_bp, **mount_options)
        return self

    def create_resource(self, **options):
        """Create a configured ``ApiResource`` subclass and mount it on ``app_or_bp``."""
        options['errors'] = {
            **self.errors,
            **(options.get('errors') or {}),
        }
        merged = {**self.defaults, **options}
        resource = self._resource_base.create_resource(**merged)
        resource.api_instance = self
        if self.app_or_bp is not None:
            resource.register(self.app_or_bp)
        else:
            self._pending_resources.append((resource, {}))
        return resource

    def create_resources(self, *resources, **options):
        """Create and mount one or more API resources.

        Supported call patterns:
            1. Single resource via keyword arguments (alias of ``create_resource``)::

                item_api = api.create_resources(model=Item, schema=ItemSchema)

            2. Multiple resource config dicts as positional arguments or a list::

                cat_api, item_api = api.create_resources(
                    {'model': Category, 'schema': CategorySchema},
                    {'model': Item, 'schema': ItemSchema},
                )

            3. Named mapping of ``resource_name={...config...}``::

                created = api.create_resources(
                    categories={'model': Category, 'schema': CategorySchema},
                    items={'model': Item, 'schema': ItemSchema},
                )
        """
        if not resources and ('model' in options or 'schema' in options):
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
            'create_resources() expects resource options (model=..., schema=...) '
            'or one or more resource configuration dictionaries.'
        )

    def register(self, resource, **mount_options):
        """Mount an existing ``ApiResource`` subclass on ``app_or_bp``."""
        resource.api_instance = self
        if getattr(resource, 'db', None) is None and self.db is not None:
            resource.db = self.db
        if self.app_or_bp is None:
            self._pending_resources.append((resource, mount_options))
            return resource
        return resource.register(self.app_or_bp, **mount_options)


# Backwards-compatible alias
ApiInstance = Api
