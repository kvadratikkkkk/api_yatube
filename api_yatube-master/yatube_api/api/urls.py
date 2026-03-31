from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework.authtoken.views import obtain_auth_token
from .views import PostViewSet, GroupViewSet, CommentViewSet

router = DefaultRouter()
router.register("posts", PostViewSet, basename="posts")
router.register("groups", GroupViewSet, basename="groups")


class PostCommentsRouter:
    def __init__(self):
        self.router = DefaultRouter()
        self.router.register(
            r"posts/(?P<post_id>\d+)/comments", CommentViewSet, basename="post-comments"
        )


urlpatterns = [
    path("api-token-auth/", obtain_auth_token),
    path("", include(router.urls)),
    path("", include(PostCommentsRouter().router.urls)),
]

# End of API URLs.
# End of file.
