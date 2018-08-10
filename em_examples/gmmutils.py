import numpy as np
import copy
from scipy.stats import multivariate_normal
from scipy import spatial, linalg
from scipy.special import logsumexp
from scipy.sparse import diags
from sklearn.mixture import GaussianMixture
from sklearn.cluster import KMeans
from sklearn.mixture.gaussian_mixture import (
    _compute_precision_cholesky, _compute_log_det_cholesky,
    _estimate_gaussian_covariances_full,
    _estimate_gaussian_covariances_tied,
    _estimate_gaussian_covariances_diag,
    _estimate_gaussian_covariances_spherical
)
from sklearn.mixture.base import (
    _check_X, check_random_state, ConvergenceWarning
)
import warnings
from SimPEG.Utils import mkvc


def ComputeDistances(a, b):

    x = mkvc(a, numDims=2)
    y = mkvc(b, numDims=2)

    n, d = x.shape
    t, d1 = y.shape

    if not d == d1:
        raise Exception('vectors must have same number of columns')

    sq_dis = np.dot(
        (x**2.),
        np.ones([d, t])
    ) + np.dot(np.ones([n, d]), (y**2.).T) - 2. * np.dot(x, y.T)

    idx = np.argmin(sq_dis, axis=1)

    return sq_dis**0.5, idx


def order_clusters_GM_weight(GMmodel, outputindex=False):
    '''
    order cluster by increasing mean for Gaussian Mixture scikit object
    '''

    indx = np.argsort(GMmodel.weights_, axis=0)[::-1]
    GMmodel.means_ = GMmodel.means_[indx].reshape(GMmodel.means_.shape)
    GMmodel.weights_ = GMmodel.weights_[indx].reshape(GMmodel.weights_.shape)
    if GMmodel.covariance_type == 'tied':
        pass
    else:
        GMmodel.precisions_ = GMmodel.precisions_[
            indx].reshape(GMmodel.precisions_.shape)
        GMmodel.covariances_ = GMmodel.covariances_[
            indx].reshape(GMmodel.covariances_.shape)
    GMmodel.precisions_cholesky_ = _compute_precision_cholesky(
        GMmodel.covariances_, GMmodel.covariance_type)

    if outputindex:
        return indx


def order_cluster(GMmodel, GMref, outputindex=False):
    order_clusters_GM_weight(GMmodel)

    idx_ref = np.ones_like(GMref.means_, dtype=bool)

    indx = []

    for i in range(GMmodel.n_components):
        _, id_dis = ComputeDistances(mkvc(GMmodel.means_[i], numDims=2),
                                     mkvc(GMref.means_[idx_ref], numDims=2))
        idrefmean = np.where(GMref.means_ == GMref.means_[
            idx_ref][id_dis])[0][0]
        indx.append(idrefmean)
        idx_ref[idrefmean] = False

    GMmodel.means_ = GMmodel.means_[indx].reshape(GMmodel.means_.shape)
    GMmodel.weights_ = GMmodel.weights_[indx].reshape(GMmodel.weights_.shape)
    if GMmodel.covariance_type == 'tied':
        pass
    else:
        GMmodel.precisions_ = GMmodel.precisions_[
            indx].reshape(GMmodel.precisions_.shape)
        GMmodel.covariances_ = GMmodel.covariances_[
            indx].reshape(GMmodel.covariances_.shape)
    GMmodel.precisions_cholesky_ = _compute_precision_cholesky(
        GMmodel.covariances_, GMmodel.covariance_type)

    if outputindex:
        return indx


def computePrecision(GMmodel):
    if GMmodel.covariance_type == 'full':
        GMmodel.precisions_ = np.empty(GMmodel.precisions_cholesky_.shape)
        for k, prec_chol in enumerate(GMmodel.precisions_cholesky_):
            GMmodel.precisions_[k] = np.dot(prec_chol, prec_chol.T)

    elif GMmodel.covariance_type == 'tied':
        GMmodel.precisions_ = np.dot(GMmodel.precisions_cholesky_,
                                     GMmodel.precisions_cholesky_.T)
    else:
        GMmodel.precisions_ = GMmodel.precisions_cholesky_ ** 2


def computeCovariance(GMmodel):
    if GMmodel.covariance_type == 'full':
        GMmodel.covariances_ = np.empty(GMmodel.covariances_cholesky_.shape)
        for k, cov_chol in enumerate(GMmodel.covariances_cholesky_):
            GMmodel.covariances_[k] = np.dot(cov_chol, cov_chol.T)

    elif GMmodel.covariance_type == 'tied':
        GMmodel.covariances_ = np.dot(GMmodel.covariances_cholesky_,
                                      GMmodel.covariances_cholesky_.T)
    else:
        GMmodel.covariances_ = GMmodel.covariances_cholesky_ ** 2


def ComputeConstantTerm(GMmodel):
    cste = 0.
    d = GMmodel.means_[0].shape[0]
    for i in range(GMmodel.n_components):
        if GMmodel.covariance_type == 'tied':
            cste += GMmodel.weights_[i] * ((1. / 2.) * np.log(((2. * np.pi)**d) * np.linalg.det(
                GMmodel.covariances_)) - np.log(GMmodel.weights_[i]))
        elif GMmodel.covariance_type == 'diag' or GMmodel.covariance_type == 'spherical':
            cste += GMmodel.weights_[i] * ((1. / 2.) * np.log(((2. * np.pi)**d) * np.linalg.det(
                GMmodel.covariances_[i] * np.eye(GMmodel.means_.shape[1]))) - np.log(GMmodel.weights_[i]))
        else:
            cste += GMmodel.weights_[i] * ((1. / 2.) * np.log(((2. * np.pi)**d) * np.linalg.det(
                GMmodel.covariances_[i])) - np.log(GMmodel.weights_[i]))
    return cste


def UpdateGaussianMixtureModel(GMmodel, GMref, alphadir=0., nu=0., kappa=0., verbose=False, update_covariances=False):

    computePrecision(GMmodel)
    order_cluster(GMmodel, GMref)

    if verbose:
        print('before update means: ', GMmodel.means_)
        print('before update weights: ', GMmodel.weights_)
        print('before update precisions: ', GMmodel.precisions_)

    for k in range(GMmodel.n_components):
        GMmodel.means_[k] = (1. / (GMmodel.weights_[k] + GMref.weights_[k] * kappa[k])) * (
            GMmodel.weights_[k] * GMmodel.means_[k] + GMref.weights_[k] * kappa[k] * GMref.means_[k])

        if GMref.covariance_type == 'tied':
            pass
        elif update_covariances:
            GMmodel.covariances_[k] = (1. / (GMmodel.weights_[k] + GMref.weights_[k] * nu[k])) * (
                GMmodel.weights_[k] * GMmodel.covariances_[k] + GMref.weights_[k] * nu[k] * GMref.covariances_[k])
        else:
            GMmodel.precisions_[k] = (
                1. / (GMmodel.weights_[k] + GMref.weights_[k] * nu[k])) * (
                GMmodel.weights_[k] * GMmodel.precisions_[k] + GMref.weights_[k] * nu[k] * GMref.precisions_[k])

        GMmodel.weights_[k] = (1. / (1. + np.sum(alphadir * GMref.weights_))) * (
            GMmodel.weights_[k] + alphadir[k] * GMref.weights_[k])

    if GMref.covariance_type == 'tied':
        if update_covariances:
            GMmodel.covariances_ = (
                1. / (1. + np.sum(GMref.weights_ * nu))) * (GMmodel.covariances_ + np.sum(GMref.weights_ * nu) * GMref.covariances_)
            GMmodel.precisions_cholesky_ = _compute_precision_cholesky(
                GMmodel.covariances_, GMmodel.covariance_type)
            computePrecision(GMmodel)
        else:
            GMmodel.precisions_ = (
                1. / (1. + np.sum(GMref.weights_ * nu))) * (GMmodel.precisions_ + np.sum(GMref.weights_ * nu) * GMref.precisions_)
            GMmodel.covariances_cholesky_ = _compute_precision_cholesky(
                GMmodel.precisions_, GMmodel.covariance_type)
            computeCovariance(GMmodel)
            GMmodel.precisions_cholesky_ = _compute_precision_cholesky(
                GMmodel.covariances_, GMmodel.covariance_type)
    elif update_covariances:
        GMmodel.precisions_cholesky_ = _compute_precision_cholesky(
            GMmodel.covariances_, GMmodel.covariance_type)
        computePrecision(GMmodel)
    else:
        GMmodel.covariances_cholesky_ = _compute_precision_cholesky(
            GMmodel.precisions_, GMmodel.covariance_type)
        computeCovariance(GMmodel)
        GMmodel.precisions_cholesky_ = _compute_precision_cholesky(
            GMmodel.covariances_, GMmodel.covariance_type)

    if verbose:
        print('after update means: ', GMmodel.means_)
        print('after update weights: ', GMmodel.weights_)
        print('after update precisions: ', GMmodel.precisions_)


class FuzzyGaussianMixtureWithPrior(GaussianMixture):

    def __init__(
        self, GMref, kappa=0., nu=0., alphadir=1., fuzzyness=2., GMinit='auto',
        init_params='kmeans', max_iter=100,
        means_init=None, n_components=3, n_init=10, precisions_init=None,
        random_state=None, reg_covar=1e-06, tol=0.001, verbose=0,
        verbose_interval=10, warm_start=False, weights_init=None,
        #**kwargs
    ):

        self.fuzzyness = fuzzyness
        self.GMref = GMref
        self.covariance_type = GMref.covariance_type
        self.kappa = np.ones(n_components) * kappa
        self.nu = np.ones(n_components) * nu
        self.alphadir = np.ones(n_components) * alphadir
        self.GMinit = GMinit

        super(FuzzyGaussianMixtureWithPrior, self).__init__(
            covariance_type=self.covariance_type, init_params=init_params,
            max_iter=max_iter, means_init=means_init, n_components=n_components,
            n_init=n_init, precisions_init=precisions_init,
            random_state=random_state, reg_covar=reg_covar, tol=tol, verbose=verbose,
            verbose_interval=verbose_interval, warm_start=warm_start, weights_init=weights_init,
            #**kwargs
        )
        # setKwargs(self, **kwargs)

    def FitFuzzyWithConjugatePrior(self, X, **kwargs):
        '''
        beta is the same size as components
        '''
        n_data, n_features = X.shape
        n_components = self.GMref.n_components
        # init scikit GM object
        # self = GaussianMixture()
        if self.GMinit == None:
            km = KMeans(n_clusters=n_components)
            km.fit(X)
            winit = (np.r_[[np.sum(km.labels_ == i) for i in range(
                n_components)]] / float(n_data)).reshape(-1, 1)
            precision_init = np.r_[
                [np.diag(np.ones(n_features)) for i in range(n_components)]]
            # self = GaussianMixture(n_components=n_components,
            # covariance_type=covariance_type)
            self.means_ = km.cluster_centers_
            self.weights_ = mkvc(winit)
            self.precisions_ = precision_init
            self.covariances_ = precision_init
            self.precisions_cholesky_ = _compute_precision_cholesky(
                self.covariances_, self.covariance_type)
        elif self.GMinit == 'auto':
            self.fit(X)
        else:
            self.means_ = copy.deepcopy(self.GMinit.means_)
            self.weights_ = copy.deepcopy(self.GMinit.weights_)
            self.precisions_ = copy.deepcopy(self.GMinit.precisions_)
            self.covariances_ = copy.deepcopy(self.GMinit.covariances_)
            self.precisions_cholesky_ = copy.deepcopy(
                self.GMinit.precisions_cholesky_)

        # Order clusters by increasing mean TODO: what happened with several properties
        # idx = order_cluster(self,self.GMref,outputindex = True)
        alphadir = self.alphadir
        kappa = self.kappa
        nu = self.nu

        # Init Membership
        # E step
        logW = np.log(np.ones((n_data, n_components)) * self.weights_)
        # print(logW)
        change = np.inf
        it = 0

        while it < self.max_iter and change > self.tol:

            change = 0.

            logP = np.zeros((n_data, n_components))

            if self.GMref.covariance_type == 'full':
                for k in range(n_components):
                    logP[:, k] = mkvc(multivariate_normal(self.means_[k], (self.covariances_[
                                      k]) * (self.fuzzyness - 1.)).logpdf(X)) + np.log(self.fuzzyness - 1) / 2.
            elif self.GMref.covariance_type == 'tied':
                raise Exception('Implementation in progress')
                # for k in range(n_components):
                #    logP[:, k] = mkvc(multivariate_normal(self.means_[k], (self.covariances_) * (
                # self.fuzzyness - 1.)).logpdf(X)) + np.log(self.fuzzyness - 1)
                # / 2.
            else:
                raise Exception('Spherical is not implemented yet')
            logWP = logW + logP
            log_r = logWP - logsumexp(logWP, axis=1, keepdims=True)
            # print(np.sum(np.exp(log_r),axis=0))
            r = np.exp(self.fuzzyness * log_r)
            sumr = np.exp(logsumexp(self.fuzzyness * log_r))
            # M step
            for k in range(n_components):

                # total Membership of the cluster
                rk = np.exp(logsumexp(self.fuzzyness * log_r[:, k]))
                # print(rk)
                if rk != 0:
                    # Update cluster center
                    muk = ((1. / (rk + kappa[k])) * (np.sum(diags(r[:, k]) * X, axis=0))) + (
                        (kappa[k] / (rk + kappa[k])) * self.GMref.means_[k])
                    if self.means_[k] != 0.:
                        change = np.maximum(
                            np.abs((self.means_[k] - muk) / self.means_[k]).max(), change)
                    else:
                        change = np.maximum(
                            np.abs((self.means_[k] - muk)).max(), change)
                    self.means_[k] = muk

                if rk != 0:
                    # Update cluster covariance
                    if self.GMref.covariance_type == 'full':
                        xmean = (np.sum(diags(r[:, k]) * X, axis=0)) / rk
                        Sk0 = (nu[k] + n_features + 2.) * \
                            self.GMref.covariances_[k]
                        Sx = np.dot((X - xmean).T,
                                    (diags(r[:, k]) * (X - xmean)))
                        Smu = ((kappa[k] * rk) / (rk + kappa[k])) * \
                            np.dot((muk - xmean).T, (muk - xmean))
                        covk = (Sk0 + Sx + Smu) / \
                            (nu[k] + rk + n_features + 2.)
                    elif self.GMref.covariance_type == 'tied':
                        Stot = (nu[0] + n_features + 2.) * \
                            self.GMref.covariances_
                        for k in range(n_components):
                            xmean = (np.sum(diags(r[:, k]) * X, axis=0)) / rk
                            Sx = np.dot((X - xmean).T,
                                        (diags(r[:, k]) * (X - xmean)))
                            Smu = ((kappa[k] * rk) / (rk + kappa[k])) * \
                                np.dot((muk - xmean).T, (muk - xmean))
                            Stot = Stot + Sx + Smu
                        covk = (Stot) / (nu[0] + n_data + n_features + 2.)

                # Regularize
                covid = self.reg_covar * np.eye(n_features)
                idx = np.abs(covk) < self.reg_covar
                # Set Off-diag to 0
                covk[idx] = 0.
                # Set On-diag to reg_covar
                covk = covk + covid * idx

                if self.GMref.covariance_type == 'full':
                    change = np.maximum(
                        np.abs((self.covariances_[k] - covk) / self.covariances_[k]).max(), change)
                    self.covariances_[k] = covk
                    # print('cov: ',covk,change)
                elif self.covariance_type == 'tied':
                    self.covariances_ = covk
                    change = np.maximum(
                        np.abs((self.covariances_ - covk) / self.covariances_).max(), change)
                # Update cluster Proportion
                # print('rk: ',rk)
                # print('total r: ', sumr)
                thetak = (rk + alphadir[k] - 1.) / \
                    (sumr + np.sum(alphadir) - n_components)
                # Real Derichlet distribution
                # thetak = (rk+beta[k]-1.) / (n_data +
                # np.sum(beta)-GMref.n_components)
                if self.weights_[k] != 0.:
                    change = np.maximum(
                        np.abs((self.weights_[k] - thetak) / self.weights_[k]).max(), change)
                else:
                    change = np.maximum(
                        np.abs((self.weights_[k] - thetak)).max(), change)
                self.weights_[k] = thetak
                # print('weights: ',thetak,change)

            self.precisions_cholesky_ = _compute_precision_cholesky(
                self.covariances_, self.covariance_type)
            computePrecision(self)
            if self.verbose:
                print('iteration: ', it)
                print('Maximum relative change done to parameters: ', change)
            it += +1
        self.n_iter_ = it


class GaussianMixtureWithPrior(GaussianMixture):

    def __init__(
        self, GMref, kappa=0., nu=0., alphadir=0.,
        prior_type='semi',  # semi or conjuguate
        init_params='kmeans', max_iter=100,
        means_init=None, n_init=10, precisions_init=None,
        random_state=None, reg_covar=1e-06, tol=0.001, verbose=0,
        verbose_interval=10, warm_start=False, weights_init=None,
        update_covariances=False,
        fixed_membership=None,
        #**kwargs
    ):

        self.n_components = GMref.n_components
        self.GMref = GMref
        self.covariance_type = GMref.covariance_type
        self.kappa = kappa * np.ones(self.n_components)
        self.nu = nu * np.ones(self.n_components)
        self.alphadir = alphadir * np.ones(self.n_components)
        self.prior_type = prior_type
        self.update_covariances = update_covariances
        self.fixed_membership = fixed_membership

        super(GaussianMixtureWithPrior, self).__init__(
            covariance_type=self.covariance_type,
            init_params=init_params,
            max_iter=max_iter,
            means_init=means_init,
            n_components=self.n_components,
            n_init=n_init,
            precisions_init=precisions_init,
            random_state=random_state,
            reg_covar=reg_covar,
            tol=tol,
            verbose=verbose,
            verbose_interval=verbose_interval,
            warm_start=warm_start,
            weights_init=weights_init,
            #**kwargs
        )
        # setKwargs(self, **kwargs)

    def fit(self, X, y=None):
        """
        MODIFIED FROM SCIKIT-LEARN FOR MAP ESTIMATE WITH PRIOR FOR EACH CLUSTER
        Estimate model parameters with the EM algorithm.
        The method fit the model `n_init` times and set the parameters with
        which the model has the largest likelihood or lower bound. Within each
        trial, the method iterates between E-step and M-step for `max_iter`
        times until the change of likelihood or lower bound is less than
        `tol`, otherwise, a `ConvergenceWarning` is raised.
        Parameters
        ----------
        X : array-like, shape (n_samples, n_features)
            List of n_features-dimensional data points. Each row
            corresponds to a single data point.
        Returns
        -------
        self
        """
        if self.verbose:
            print('modified from scikit-learn')

        X = _check_X(X, self.n_components)
        self._check_initial_parameters(X)

        # if we enable warm_start, we will have a unique initialisation
        do_init = not(self.warm_start and hasattr(self, 'converged_'))
        n_init = self.n_init if do_init else 1

        max_lower_bound = -np.infty
        self.converged_ = False

        random_state = check_random_state(self.random_state)

        n_samples, _ = X.shape
        for init in range(n_init):
            self._print_verbose_msg_init_beg(init)

            if do_init:
                self._initialize_parameters(X, random_state)
                self.lower_bound_ = -np.infty

            for n_iter in range(self.max_iter):
                prev_lower_bound = self.lower_bound_

                log_prob_norm, log_resp = self._e_step(X)
                if self.fixed_membership is not None:
                    new_log_resp = -(np.inf) * np.ones_like(log_resp)
                    new_log_resp[
                        np.arange(len(new_log_resp)), self.fixed_membership] = 0.
                    log_resp = new_log_resp
                self._m_step(X, log_resp)
                UpdateGaussianMixtureModel(
                    self, self.GMref,
                    alphadir=self.alphadir,
                    nu=self.nu,
                    kappa=self.kappa,
                    verbose=self.verbose,
                    update_covariances=self.update_covariances,
                )
                self.lower_bound_ = self._compute_lower_bound(
                    log_resp, log_prob_norm)

                change = self.lower_bound_ - prev_lower_bound
                self._print_verbose_msg_iter_end(n_iter, change)

                if abs(change) < self.tol:
                    self.converged_ = True
                    break

            self._print_verbose_msg_init_end(self.lower_bound_)

            if self.lower_bound_ > max_lower_bound:
                max_lower_bound = self.lower_bound_
                best_params = self._get_parameters()
                best_n_iter = n_iter

        if not self.converged_:
            warnings.warn('Initialization %d did not converge. '
                          'Try different init parameters, '
                          'or increase max_iter, tol '
                          'or check for degenerate data.'
                          % (init + 1), ConvergenceWarning)

        self._set_parameters(best_params)
        self.n_iter_ = best_n_iter
        self.last_step_change = change

        return self

