# Demo resume generators

Utilities that produce the demonstration resume corpus. They are development
tooling and are not imported by the application.

The corpus they write is consumed from `backend/demo_resumes/`, deliberately:
`backend/` is the Docker build context, and a corpus living at the repository
root never reached the image. Production once ran on two candidates against
thirty while every deploy reported success, because the seed found nothing and
exited 0.

Do not move the corpus back out of `backend/`.
