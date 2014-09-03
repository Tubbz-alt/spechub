#-*- coding: utf-8 -*-

"""
 (c) 2014 - Copyright Red Hat Inc

 Authors:
   Pierre-Yves Chibon <pingou@pingoured.fr>

"""

import flask
import os
import shutil
import tempfile
from math import ceil

import pygit2
from sqlalchemy.exc import SQLAlchemyError
from pygments import highlight
from pygments.lexers import guess_lexer
from pygments.lexers.text import DiffLexer
from pygments.formatters import HtmlFormatter


import spechub.doc_utils
import spechub.lib
import spechub.ui.forms
from spechub import (APP, SESSION, LOG, __get_file_in_tree, cla_required,
                    is_repo_admin)


@APP.route('/<repo>/request-pulls')
@APP.route('/fork/<username>/<repo>/request-pulls')
def request_pulls(repo, username=None):
    """ Request pulling the changes from the fork into the project.
    """
    status = flask.request.args.get('status', True)

    reponame = os.path.join(APP.config['GIT_FOLDER'], repo + '.git')
    if username:
        reponame = os.path.join(
            APP.config['FORK_FOLDER'], username, repo + '.git')

    if not repo:
        flask.abort(404, 'Project not found')

    project = spechub.lib.get_or_create_project(SESSION, repo, username)

    if status is False or str(status).lower() == 'closed':
        requests = spechub.lib.get_pull_requests(
            SESSION, project_id=project.id, status=False)
    else:
        requests = spechub.lib.get_pull_requests(
            SESSION, project_id=project.id, status=status)

    return flask.render_template(
        'requests.html',
        select='requests',
        repo=repo,
        username=username,
        requests=requests,
        status=status,
        forks=spechub.lib.get_forks(SESSION, project),
    )


@APP.route('/<repo>/request-pull/<requestid>')
@APP.route('/fork/<username>/<repo>/request-pull/<requestid>')
def request_pull(repo, requestid, username=None):
    """ Request pulling the changes from the fork into the project.
    """

    request = spechub.lib.get_pull_request(
        SESSION, project=repo, requestid=requestid)

    if not request:
        flask.abort(404, 'Pull-request not found')

    project = request.repo_from

    reponame = os.path.join(APP.config['FORK_FOLDER'], project.path)
    if not request.status:
        project = request.repo
        reponame = os.path.join(APP.config['GIT_FOLDER'], project.path)

    if not os.path.exists(reponame):
        flask.abort(404, 'Project not found')

    repo_obj = pygit2.Repository(reponame)

    if project.parent:
        parentname = os.path.join(
            APP.config['GIT_FOLDER'], project.parent.path)
    else:
        parentname = os.path.join(
            APP.config['GIT_FOLDER'], project.path)
    orig_repo = pygit2.Repository(parentname)

    diff_commits = []
    diffs = []
    repo_commit = repo_obj[request.stop_id]
    if not repo_obj.is_empty and not orig_repo.is_empty:
        orig_commit = orig_repo[
            orig_repo.lookup_branch('master').get_object().hex]

        master_commits = [
            commit.oid.hex
            for commit in orig_repo.walk(
                orig_repo.lookup_branch('master').get_object().hex,
                pygit2.GIT_SORT_TIME)
        ]

        repo_commit = repo_obj[request.stop_id]

        for commit in repo_obj.walk(
                request.stop_id, pygit2.GIT_SORT_TIME):
            if request.status and commit.oid.hex in master_commits:
                break
            elif not request.status and commit.oid.hex == request.start_id:
                break
            diff_commits.append(commit)
            diffs.append(
                repo_obj.diff(
                    repo_obj.revparse_single(commit.parents[0].oid.hex),
                    repo_obj.revparse_single(commit.oid.hex)
                )
            )

    elif orig_repo.is_empty:
        orig_commit = None
        diff = repo_commit.tree.diff_to_tree(swap=True)
    else:
        flask.flash(
            'Fork is empty, there are no commits to request pulling',
            'error')
        return flask.redirect(flask.url_for(
            'view_repo', username=username, repo=repo.name))

    html_diffs = []
    for diff in diffs:
        html_diffs.append(
            highlight(
                diff.patch,
                DiffLexer(),
                HtmlFormatter(
                    noclasses=True,
                    style="tango",)
            )
        )

    return flask.render_template(
        'pull_request.html',
        select='requests',
        requestid=requestid,
        repo=repo,
        username=username,
        request=request,
        #repo_admin=is_repo_admin(request.repo),
        repo_obj=repo_obj,
        orig_repo=orig_repo,
        diff_commits=diff_commits,
        diffs=diffs,
        html_diffs=html_diffs,
        forks=spechub.lib.get_forks(SESSION, request.repo),
    )


@APP.route('/<repo>/request-pull/<requestid>/comment/<commit>/<row>',
           methods=('GET', 'POST'))
@APP.route('/fork/<username>/<repo>/request-pull/<requestid>/comment/'
           '<commit>/<row>', methods=('GET', 'POST'))
def pull_request_add_comment(repo, requestid, commit, row, username=None):
    """ Add a comment to a commit in a pull-request.
    """
    repo = spechub.lib.get_project(SESSION, repo, user=username)

    if not repo:
        flask.abort(404, 'Project not found')

    request = spechub.lib.get_pull_request(
        SESSION, project_id=repo.id, requestid=requestid)
    repo = request.repo_from

    if not request:
        flask.abort(404, 'Pull-request not found')

    form = spechub.forms.AddPullRequestCommentForm()
    form.commit.data = commit
    form.requestid.data = requestid
    form.row.data = row

    if form.validate_on_submit():
        comment = form.comment.data

        try:
            message = spechub.lib.add_pull_request_comment(
                SESSION,
                request=request,
                commit=commit,
                row=row,
                comment=comment,
                user=flask.g.fas_user.username,
            )
            SESSION.commit()
            flask.flash(message)
        except SQLAlchemyError, err:  # pragma: no cover
            SESSION.rollback()
            flask.flash(str(err), 'error')

        return flask.redirect(flask.url_for(
            'request_pull', username=username,
            repo=repo.name, requestid=requestid))

    return flask.render_template(
        'pull_request_comment.html',
        select='requests',
        requestid=requestid,
        repo=repo,
        username=username,
        commit=commit,
        row=row,
        form=form,
    )



@APP.route('/<repo>/request-pull/merge/<requestid>')
@APP.route('/fork/<username>/<repo>/request-pull/merge/<requestid>')
def merge_request_pull(repo, requestid, username=None):
    """ Request pulling the changes from the fork into the project.
    """
    request = spechub.lib.get_pull_request(
        SESSION, project=repo, requestid=requestid)
    repo = request.repo_from

    if not request:
        flask.abort(404, 'Pull-request not found')

    reponame = os.path.join(APP.config['FORK_FOLDER'], repo.path)

    if not os.path.exists(reponame):
        flask.abort(404, 'Project not found')

    if not is_repo_admin(repo):
        flask.abort(
            403,
            'You are not allowed to merge pull-request for this project')

    error_output = flask.url_for(
        'request_pull', repo=repo.name, requestid=requestid)
    if username:
        error_output = flask.url_for(
            'fork_request_pull',
            repo=repo.name,
            requestid=requestid,
            username=username)

    # Get the fork
    if request.repo_from.is_fork:
        repopath = os.path.join(
            APP.config['FORK_FOLDER'], request.repo_from.path)
    else:
        repopath = os.path.join(
            APP.config['GIT_FOLDER'], request.repo_from.path)
    fork_obj = pygit2.Repository(repopath)

    # Get the original repo
    parentpath = os.path.join(APP.config['GIT_FOLDER'], request.repo.path)
    orig_repo = pygit2.Repository(parentpath)

    # Clone the original repo into a temp folder
    newpath = tempfile.mkdtemp()
    new_repo = pygit2.clone_repository(parentpath, newpath)

    repo_commit = fork_obj[request.stop_id]

    ori_remote = new_repo.remotes[0]
    # Add the fork as remote repo
    reponame = '%s_%s' % (request.user.user, repo.name)
    remote = new_repo.create_remote(reponame, repopath)

    # Fetch the commits
    remote.fetch()

    merge = new_repo.merge(repo_commit.oid)
    master_ref = new_repo.lookup_reference('HEAD').resolve()

    refname = '%s:%s' % (master_ref.name, master_ref.name)
    if merge.is_uptodate:
        flask.flash('Nothing to do, changes were already merged', 'error')
        spechub.lib.close_pull_request(SESSION, request)
        SESSION.commit()
        return flask.redirect(error_output)
    elif merge.is_fastforward:
        master_ref.target = merge.fastforward_oid
        ori_remote.push(refname)
        flask.flash('Changes merged!')
    else:
        new_repo.index.write()
        try:
            tree = new_repo.index.write_tree()
        except pygit2.GitError:
            shutil.rmtree(newpath)
            flask.flash('Merge conflicts!', 'error')
            return flask.redirect(flask.url_for(
                'request_pull',
                repo=repo.name,
                username=username,
                requestid=requestid))
        head = new_repo.lookup_reference('HEAD').get_object()
        commit = new_repo[head.oid]
        sha = new_repo.create_commit(
            'refs/heads/master',
            repo_commit.author,
            repo_commit.committer,
            'Merge #%s `%s`' % (request.id, request.title),
            tree,
            [head.hex, repo_commit.oid.hex])
        ori_remote.push(refname)
        flask.flash('Changes merged!')

    # Update status
    spechub.lib.close_pull_request(SESSION, request)
    SESSION.commit()
    shutil.rmtree(newpath)

    return flask.redirect(flask.url_for('view_repo', repo=repo.name))


## Specific actions


@APP.route('/do_fork/<repo>')
@cla_required
def fork_project(repo, username=None):
    """ Fork the project specified into the user's namespace
    """
    reponame = os.path.join(APP.config['GIT_FOLDER'], repo + '.git')
    if username:
        reponame = os.path.join(
            APP.config['FORK_FOLDER'], username, repo + '.git')

    if not os.path.exists(reponame):
        flask.abort(404, 'Project not found')

    repo_obj = pygit2.Repository(reponame)

    try:
        message = spechub.lib.fork_project(
            session=SESSION,
            repo=repo,
            gitfolder=APP.config['GIT_FOLDER'],
            forkfolder=APP.config['FORK_FOLDER'],
            username=flask.g.fas_user.username)

        SESSION.commit()
        #generate_gitolite_acls()
        flask.flash(message)
        return flask.redirect(
            flask.url_for(
                'view_repo',
                username=flask.g.fas_user.username,
                repo=repo)
        )
    except spechub.exceptions.SpecHubException, err:
        flask.flash(str(err), 'error')
    except SQLAlchemyError, err:  # pragma: no cover
        SESSION.rollback()
        flask.flash(str(err), 'error')

    return flask.redirect(flask.url_for('view_repo', repo=repo))


@APP.route('/fork/<username>/<repo>/request-pull/new',
           methods=('GET', 'POST'))
@APP.route('/fork/<username>/<repo>/request-pull/new/<commitid>',
           methods=('GET', 'POST'))
@cla_required
def new_request_pull(repo, username, commitid=None):
    """ Request pulling the changes from the fork into the project.
    """
    reponame = os.path.join(
        APP.config['FORK_FOLDER'], username, repo + '.git')
    if not os.path.exists(reponame):
        flask.abort(404, 'Project not found')

    parentname = os.path.join(APP.config['GIT_FOLDER'], repo + '.git')
    if not os.path.exists(parentname):
        flask.abort(404, 'Fork not found')

    repo_obj = pygit2.Repository(reponame)
    orig_repo = pygit2.Repository(parentname)

    branchname = flask.request.args.get('branch', 'master')
    if commitid is None:
        commitid = repo_obj.head.target
        if branchname:
            branch = repo_obj.lookup_branch(branchname)
            commitid = branch.get_object().hex

    diff_commits = []
    diffs = []
    if not repo_obj.is_empty and not orig_repo.is_empty:
        orig_commit = orig_repo[
            orig_repo.lookup_branch(branchname).get_object().hex]

        master_commits = [
            commit.oid.hex
            for commit in orig_repo.walk(
                orig_repo.lookup_branch(branchname).get_object().hex,
                pygit2.GIT_SORT_TIME)
        ]

        repo_commit = repo_obj[commitid]

        for commit in repo_obj.walk(
                repo_commit.oid.hex, pygit2.GIT_SORT_TIME):
            if commit.oid.hex in master_commits:
                break
            diff_commits.append(commit)
            diffs.append(
                repo_obj.diff(
                    repo_obj.revparse_single(commit.parents[0].oid.hex),
                    repo_obj.revparse_single(commit.oid.hex)
                )
            )

    elif orig_repo.is_empty:
        orig_commit = None
        repo_commit = repo_obj[repo_obj.head.target]
        diff = repo_commit.tree.diff_to_tree(swap=True)
    else:
        flask.flash(
            'Fork is empty, there are no commits to request pulling',
            'error')
        return flask.redirect(flask.url_for(
            'view_repo', username=username, repo=repo.name))

    html_diffs = []
    for diff in diffs:
        html_diffs.append(
            highlight(
                diff.patch,
                DiffLexer(),
                HtmlFormatter(
                    noclasses=True,
                    style="tango",)
            )
        )

    form = spechub.ui.forms.RequestPullForm()
    if form.validate_on_submit():
        try:
            if orig_commit:
                orig_commit = orig_commit.oid.hex

            parent = repo

            message = spechub.lib.new_pull_request(
                SESSION,
                repo=parent,
                repo_from=repo,
                title=form.title.data,
                start_id=orig_commit,
                stop_id=repo_commit.oid.hex,
                user=flask.g.fas_user.username,
            )
            SESSION.commit()
            flask.flash(message)

            return flask.redirect(
                flask.url_for('request_pulls', username=None, repo=parent))
        except spechub.exceptions.SpecHubException, err:
            flask.flash(str(err), 'error')
        except SQLAlchemyError, err:  # pragma: no cover
            SESSION.rollback()
            flask.flash(str(err), 'error')

    return flask.render_template(
        'pull_request.html',
        select='requests',
        repo=repo,
        username=username,
        commitid=commitid,
        repo_obj=repo_obj,
        orig_repo=orig_repo,
        diff_commits=diff_commits,
        diffs=diffs,
        html_diffs=html_diffs,
        form=form,
    )
